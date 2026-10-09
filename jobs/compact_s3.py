import asyncio
import datetime
import json
import os
import uuid

import aioboto3
from botocore.config import Config
import pyarrow as pa
import pyarrow.parquet as pq
from shared.logging import Logger

from config.settings import settings
from interfaces.compact_s3 import ListOfJobs, ParseResult, S3BatchFile

log = Logger("Compactar S3")

# Umbrales y límites de compactación
MIN_BYTES_FOR_COMPACTION = 300 * 1024 * 1024  # 300 MB
MIN_INACTIVITY_SECONDS = 60 * 30             # 30 Minutos
CHUNK_WRITE_SIZE = 20_000                    # Número de registros por lote intermedio en RAM antes de escribir a disco
MAX_FILE_SIZE_BYTES = 250 * 1024 * 1024      # 250 MB. Cuando el archivo en disco supera esta cota, se cierra y sube
TOLERANCIA_COLA_BYTES = 50 * 1024 * 1024     # 50 MB. Si la cola remanente es menor a este tamaño, se absorbe en el archivo actual

# Directorio temporal local en el workspace
JOBS_ROOT = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(JOBS_ROOT, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)


def _get_client(
    endpoint_url: str | None = None,
    region: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None
):
    """
    Fábrica asíncrona de clientes S3 desacoplada para admitir almacenamiento multinivel (MinIO vs AWS S3).
    """
    config = Config(
        max_pool_connections=50,
        retries={'max_attempts': 5, 'mode': 'standard'}
    )
    session = aioboto3.Session()
    try:
        return session.client(
            "s3",
            region_name=region or settings.effective_raw_region,
            endpoint_url=endpoint_url or settings.effective_raw_endpoint_url,
            aws_access_key_id=access_key or settings.effective_raw_access_key,
            aws_secret_access_key=secret_key or settings.effective_raw_secret_key,
            config=config
        )
    except Exception as e:
        log.error(f"Fallo crítico al inicializar la sesión S3 ({endpoint_url}): {e}")
        raise


def _map_keys(response: dict) -> S3BatchFile:
    return S3BatchFile(
        key=response['Key'],
        size=response['Size'],
        last_modified=response['LastModified']
    )


async def _is_batch_compacted(client, key: str, bucket_name: str | None = None) -> bool:
    """
    Consulta si el objeto S3 crudo ya tiene la etiqueta compacted=true.
    """
    bucket = bucket_name or settings.effective_raw_bucket_name
    try:
        tagging = await client.get_object_tagging(
            Bucket=bucket,
            Key=key
        )
        tag_set = tagging.get('TagSet', [])
        return any(tag.get('Key') == 'compacted' and tag.get('Value') == 'true' for tag in tag_set)
    except Exception:
        return False


async def tag_batches_as_compacted(client, batches: list[S3BatchFile], bucket_name: str | None = None) -> None:
    """
    Aplica la etiqueta compacted=true en S3 a los lotes crudos procesados.
    """
    bucket = bucket_name or settings.effective_raw_bucket_name
    for batch in batches:
        try:
            await client.put_object_tagging(
                Bucket=bucket,
                Key=batch.key,
                Tagging={'TagSet': [{'Key': 'compacted', 'Value': 'true'}]}
            )
        except Exception as e:
            log.error(f"Error al aplicar etiqueta S3 a {batch.key}: {e}")


async def get_list_of_jobs(client, bucket_name: str | None = None) -> list[str]:
    bucket = bucket_name or settings.effective_raw_bucket_name
    try:
        paginator = client.get_paginator('list_objects_v2')
        log.info(
            f"Iniciando listado de objetos en el bucket: {bucket}")

        all_prefixes = []
        async for page in paginator.paginate(
            Bucket=bucket,
            Prefix=settings.s3_prefix_raw_data + "/",
            Delimiter="/"
        ):
            for prefix in page.get('CommonPrefixes', []):
                all_prefixes.append(prefix['Prefix'])

        log.info(f"Se han encontrado {len(all_prefixes)} carpetas de Jobs.")
        return all_prefixes
    except Exception as e:
        log.error(f"Error crítico al obtener la lista de Jobs en S3 ({bucket}): {e}")
        raise


async def get_list_of_batches(
    client,
    job_prefix: str,
    semaphore: asyncio.Semaphore,
    bucket_name: str | None = None
) -> ListOfJobs:
    bucket = bucket_name or settings.effective_raw_bucket_name
    async with semaphore:
        try:
            paginator = client.get_paginator('list_objects_v2')
            total_tasks = 0
            total_bytes = 0
            last_modified = None
            uncompacted_batches = []

            async for page in paginator.paginate(Bucket=bucket, Prefix=job_prefix):
                contents = page.get('Contents', [])
                for batch in contents:
                    if batch['Key'] == job_prefix:
                        continue
                    
                    is_compacted = await _is_batch_compacted(client, batch['Key'], bucket_name=bucket)
                    if is_compacted:
                        continue

                    total_tasks += 1
                    total_bytes += batch['Size']
                    task_date = batch['LastModified']

                    if last_modified is None or task_date > last_modified:
                        last_modified = task_date

                    mapped = _map_keys(batch)
                    mapped.is_compacted = False
                    uncompacted_batches.append(mapped)

            if last_modified is None:
                last_modified = datetime.datetime.now(datetime.timezone.utc)

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(
                    tzinfo=datetime.timezone.utc)

            return ListOfJobs(
                prefix=job_prefix,
                total_tasks=total_tasks,
                total_bytes=total_bytes,
                last_modified=last_modified,
                inactive_time=now_utc - last_modified,
                batches=uncompacted_batches
            )
        except Exception as e:
            log.error(
                f"Error crítico al procesar metadatos del Job {job_prefix} en {bucket}: {e}")
            raise


def _write_chunk_to_file(writer: pq.ParquetWriter, schema: pa.Schema, buffer_data: dict) -> None:
    """
    Convierte el búfer de RAM en una tabla PyArrow y la escribe al disco local.
    """
    table = pa.Table.from_pydict(buffer_data, schema=schema)
    writer.write_table(table)


async def _upload_compacted_file(
    client,
    local_path: str,
    job_id: str,
    part_idx: int,
    first_date: datetime.datetime,
    bucket_name: str | None = None
) -> None:
    """
    Sube el archivo Parquet local al almacenamiento analítico de forma asíncrona.
    """
    bucket = bucket_name or settings.effective_target_bucket_name
    s3_key = (
        f"{settings.s3_prefix_compacted_data}/"
        f"job_id={job_id}/year={first_date.year}/month={first_date.month}/"
        f"day={first_date.day}/part-{part_idx:04d}-{uuid.uuid4().hex[:6]}.parquet"
    )
    log.info(f"Subiendo archivo Parquet compactado Parte {part_idx} a {bucket}: {s3_key}")
    
    await client.upload_file(
        Filename=local_path,
        Bucket=bucket,
        Key=s3_key
    )


async def clear_job(client, job: ListOfJobs, bucket_name: str | None = None) -> None:
    """
    Purga los archivos crudos de un Job en la Landing Zone (MinIO/S3) para liberar espacio.
    """
    if not job or not job.batches:
        return
    bucket = bucket_name or settings.effective_raw_bucket_name
    try:
        log.info(
            f"Iniciando purga de la Landing Zone para el Job: {job.prefix} en bucket [{bucket}]")
        objects_to_delete = [{'Key': batch.key} for batch in job.batches]

        for i in range(0, len(objects_to_delete), 1000):
            chunk = objects_to_delete[i:i+1000]
            await client.delete_objects(
                Bucket=bucket,
                Delete={'Objects': chunk}
            )
        log.info(
            f"Todos los archivos ({len(objects_to_delete)}) del Job {job.prefix} fueron eliminados de [{bucket}].")
    except Exception as e:
        log.error(
            f"Error crítico en la purga del Job: {job.prefix} en [{bucket}] | Error: {e}")


async def purge_expired_raw_data(client, bucket_name: str | None = None) -> int:
    """
    Examina la Landing Zone y elimina objetos crudos cuya fecha de modificación
    supere los días de retención configurados (raw_data_retention_days).
    """
    bucket = bucket_name or settings.effective_raw_bucket_name
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        retention_threshold = now_utc - datetime.timedelta(
            days=settings.raw_data_retention_days
        )
        log.info(
            f"Iniciando inspección de retención (TTL {settings.raw_data_retention_days} días) en Landing Zone [{bucket}]. "
            f"Purgando objetos crudos anteriores a: {retention_threshold.isoformat()}"
        )

        paginator = client.get_paginator('list_objects_v2')
        expired_keys = []

        async for page in paginator.paginate(
            Bucket=bucket,
            Prefix=settings.s3_prefix_raw_data + "/"
        ):
            contents = page.get('Contents', [])
            for obj in contents:
                last_mod = obj['LastModified']
                if last_mod.tzinfo is None:
                    last_mod = last_mod.replace(tzinfo=datetime.timezone.utc)
                if last_mod < retention_threshold:
                    expired_keys.append({'Key': obj['Key']})

        if not expired_keys:
            log.info(
                f"No se encontraron objetos caducados en la Landing Zone [{bucket}].")
            return 0

        log.info(
            f"Se encontraron {len(expired_keys)} objeto(s) caducados. Procediendo a purgar...")
        total_deleted = 0

        for i in range(0, len(expired_keys), 1000):
            chunk = expired_keys[i:i + 1000]
            await client.delete_objects(
                Bucket=bucket,
                Delete={'Objects': chunk}
            )
            total_deleted += len(chunk)

        log.info(
            f"Purga de retención completada. Total de objetos raw eliminados: {total_deleted}")
        return total_deleted
    except Exception as e:
        log.error(
            f"Error crítico durante la purga de retención de datos raw en [{bucket}]: {e}")
        raise


def _is_job_eligible(job: ListOfJobs) -> bool:
    """
    Evalúa si un job cumple con las condiciones mínimas para ser compactado
    (supera el umbral de tamaño de bytes crudos o lleva inactivo más del tiempo establecido).
    """
    return job.total_bytes >= MIN_BYTES_FOR_COMPACTION or job.inactive_time.total_seconds() >= MIN_INACTIVITY_SECONDS


def _get_compaction_schema() -> pa.Schema:
    """
    Retorna el esquema de PyArrow para la tabla del Data Lake compactado.
    """
    return pa.schema([
        pa.field("task_id", pa.string()),
        pa.field("url", pa.string()),
        pa.field("date", pa.timestamp("us")),
        # Resiliencia analítica absoluta ante esquemas polimórficos
        pa.field("data", pa.string())
    ])


class JobCompactor:
    """
    Clase de estado que encapsula la compactación incremental de registros 
    para reducir la complejidad cognitiva de la función principal.
    Admite almacenamiento multinivel (source_client para lectura y target_client para subida).
    """
    def __init__(
        self,
        source_client,
        target_client,
        job: ListOfJobs,
        schema: pa.Schema,
        job_id: str,
        target_bucket: str | None = None
    ):
        self.source_client = source_client
        self.target_client = target_client
        self.job = job
        self.schema = schema
        self.job_id = job_id
        self.target_bucket = target_bucket or settings.effective_target_bucket_name
        
        self.part_counter = 0
        self.rows_in_current_file = 0
        self.rows_in_chunk = 0
        self.current_chunk_buffer = {field.name: [] for field in schema}
        self.local_file_path = os.path.join(TMP_DIR, f"compact_{job_id}_{self.part_counter}.parquet")
        self.writer = None
        self.first_date = None

    def initialize_writer_if_needed(self, parse_result: ParseResult) -> None:
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.local_file_path, schema=self.schema, compression='zstd')
            first_date = parse_result.task.created_at
            if first_date.tzinfo is None:
                first_date = first_date.replace(tzinfo=datetime.timezone.utc)
            self.first_date = first_date

    def append_record(self, parse_result: ParseResult) -> None:
        self.current_chunk_buffer["task_id"].append(parse_result.task.task_id)
        self.current_chunk_buffer["url"].append(str(parse_result.task.url))
        self.current_chunk_buffer["date"].append(parse_result.task.created_at)
        self.current_chunk_buffer["data"].append(json.dumps(parse_result.data))
        self.rows_in_chunk += 1
        self.rows_in_current_file += 1

    async def flush_current_chunk(self) -> None:
        if self.rows_in_chunk > 0:
            await asyncio.to_thread(_write_chunk_to_file, self.writer, self.schema, self.current_chunk_buffer)
            self.current_chunk_buffer = {field.name: [] for field in self.schema}
            self.rows_in_chunk = 0

    async def check_and_perform_split(self, b_idx: int) -> None:
        if not os.path.exists(self.local_file_path):
            return
        if os.path.getsize(self.local_file_path) < MAX_FILE_SIZE_BYTES:
            return

        batches_restantes = self.job.batches[b_idx + 1:]
        bytes_remanentes_s3 = sum(b.size for b in batches_restantes)

        if bytes_remanentes_s3 > TOLERANCIA_COLA_BYTES:
            await self.close_and_upload_current_file()
            self.part_counter += 1
            self.rows_in_current_file = 0
            self.local_file_path = os.path.join(TMP_DIR, f"compact_{self.job_id}_{self.part_counter}.parquet")

    async def close_and_upload_current_file(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        if self.rows_in_current_file > 0:
            await _upload_compacted_file(
                self.target_client,
                self.local_file_path,
                self.job_id,
                self.part_counter,
                self.first_date,
                bucket_name=self.target_bucket
            )
            if os.path.exists(self.local_file_path):
                os.remove(self.local_file_path)
            self.part_counter += 1
            self.rows_in_current_file = 0

    def clean_local_file(self) -> None:
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:
                pass
        if os.path.exists(self.local_file_path):
            try:
                os.remove(self.local_file_path)
            except Exception:
                pass


async def process_job(
    source_client,
    target_client,
    job: ListOfJobs,
    compaction_semaphore: asyncio.Semaphore,
    raw_bucket: str | None = None,
    target_bucket: str | None = None
) -> ListOfJobs | None:
    source_b = raw_bucket or settings.effective_raw_bucket_name
    target_b = target_bucket or settings.effective_target_bucket_name

    async with compaction_semaphore:
        if not _is_job_eligible(job):
            log.info(
                f"Ignorando Job {job.prefix} | No cumple umbrales mínimos de tamaño o inactividad.")
            return None

        job_id = job.prefix.rstrip("/").split("/")[-1].split("=")[-1]
        schema = _get_compaction_schema()
        compactor = JobCompactor(source_client, target_client, job, schema, job_id, target_bucket=target_b)

        try:
            log.info(f"Iniciando compactación incremental en disco de Job: {job.prefix} (Origen: {source_b} -> Destino: {target_b})")

            for b_idx, batch in enumerate(job.batches):
                response = await source_client.get_object(Bucket=source_b, Key=batch.key)
                stream = response['Body']
                try:
                    async for line in stream.iter_lines():
                        if not line:
                            continue

                        line_data = json.loads(line.decode('utf-8'))
                        parse_result = ParseResult.model_validate(line_data)

                        compactor.initialize_writer_if_needed(parse_result)
                        compactor.append_record(parse_result)

                        if compactor.rows_in_chunk >= CHUNK_WRITE_SIZE:
                            await compactor.flush_current_chunk()
                            await compactor.check_and_perform_split(b_idx)
                finally:
                    stream.close()

            # Guardar registros remanentes finales y subir Parquet al destino
            await compactor.flush_current_chunk()
            await compactor.close_and_upload_current_file()

            if compactor.part_counter == 0:
                log.info(f"Job {job_id} no contenía registros analíticos válidos.")
                compactor.clean_local_file()
                return None

            # Estrategia de gestión de disco local (Delete-on-Success vs Tagging)
            if settings.delete_raw_after_compaction:
                await clear_job(source_client, job, bucket_name=source_b)
                log.info(f"Purga Delete-on-Success completada para Job [{job_id}] en [{source_b}]. Disco local liberado.")
            else:
                await tag_batches_as_compacted(source_client, job.batches, bucket_name=source_b)
                log.info(f"Lotes de Job [{job_id}] etiquetados con compacted=true en [{source_b}].")

            log.info(f"Job {job_id} consolidado globalmente con éxito en {compactor.part_counter} parte(s) Parquet ZSTD.")
            return job

        except Exception as e:
            log.error(f"Fallo crítico al compactar Job: {job.prefix} | Error: {e}")
            compactor.clean_local_file()
            raise


async def main():
    log.info("Iniciando micro-orquestador de compactación S3 Multinivel...")
    raw_ctx = _get_client(
        endpoint_url=settings.effective_raw_endpoint_url,
        region=settings.effective_raw_region,
        access_key=settings.effective_raw_access_key,
        secret_key=settings.effective_raw_secret_key
    )
    target_ctx = _get_client(
        endpoint_url=settings.effective_target_endpoint_url,
        region=settings.effective_target_region,
        access_key=settings.effective_target_access_key,
        secret_key=settings.effective_target_secret_key
    )

    async with raw_ctx as raw_client, target_ctx as target_client:
        try:
            # Fase 1: Descubrimiento de Carpetas Virtuales en Landing Zone
            job_prefixes = await get_list_of_jobs(raw_client, bucket_name=settings.effective_raw_bucket_name)
            if job_prefixes:
                # Fase 2: Análisis Concurrente de Metadatos (I/O Bound)
                metadata_semaphore = asyncio.Semaphore(20)
                metadata_tasks = [
                    get_list_of_batches(raw_client, prefix, metadata_semaphore, bucket_name=settings.effective_raw_bucket_name)
                    for prefix in job_prefixes
                ]

                analyzed_jobs: list[ListOfJobs] = await asyncio.gather(*metadata_tasks, return_exceptions=False)
                log.info(
                    f"Análisis finalizado. {len(analyzed_jobs)} Jobs validados en Landing Zone.")

                # Fase 3: Procesamiento de Compactación Concurrente (MinIO -> AWS S3)
                compaction_semaphore = asyncio.Semaphore(3)
                compaction_tasks = [
                    process_job(
                        raw_client,
                        target_client,
                        job,
                        compaction_semaphore,
                        raw_bucket=settings.effective_raw_bucket_name,
                        target_bucket=settings.effective_target_bucket_name
                    )
                    for job in analyzed_jobs
                ]

                raw_results = await asyncio.gather(*compaction_tasks, return_exceptions=False)

                jobs_successfully_compacted = [
                    job for job in raw_results if job is not None]
                log.info(
                    f"Proceso analítico finalizado. {len(jobs_successfully_compacted)} jobs consolidados en Parquet ZSTD.")
            else:
                log.info("No se encontraron particiones de datos en la Landing Zone.")

            # Fase 4: Purga de Retención TTL en Landing Zone
            log.info("Iniciando fase de gestión de retención y expiración TTL...")
            await purge_expired_raw_data(raw_client, bucket_name=settings.effective_raw_bucket_name)
            log.info("Proceso global de compactación y retención finalizado con éxito.")

        except Exception as e:
            log.error(
                f"Error catastrófico en el proceso de compactación global: {str(e)}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
