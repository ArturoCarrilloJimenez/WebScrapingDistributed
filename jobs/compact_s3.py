import asyncio
import datetime
import json
import os
from typing import List, Dict, Any
import uuid

import aioboto3
from botocore.config import Config
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow.fs import S3FileSystem
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


def _get_client():
    config = Config(
        max_pool_connections=50,
        retries={'max_attempts': 5, 'mode': 'standard'}
    )
    session = aioboto3.Session()
    try:
        return session.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=config
        )
    except Exception as e:
        log.error(f"Fallo crítico al inicializar la sesión S3: {e}")
        raise


def _map_keys(response: dict) -> S3BatchFile:
    return S3BatchFile(
        key=response['Key'],
        size=response['Size'],
        last_modified=response['LastModified']
    )


async def get_list_of_jobs(client) -> List[str]:
    try:
        paginator = client.get_paginator('list_objects_v2')
        log.info(
            f"Iniciando listado de objetos en el bucket: {settings.s3_bucket_name}")

        all_prefixes = []
        async for page in paginator.paginate(
            Bucket=settings.s3_bucket_name,
            Prefix=settings.s3_prefix_raw_data + "/",
            Delimiter="/"
        ):
            for prefix in page.get('CommonPrefixes', []):
                all_prefixes.append(prefix['Prefix'])

        log.info(f"Se han encontrado {len(all_prefixes)} carpetas de Jobs.")
        return all_prefixes
    except Exception as e:
        log.error(f"Error crítico al obtener la lista de Jobs en S3: {e}")
        raise


async def get_list_of_batches(client, job_prefix: str, semaphore: asyncio.Semaphore) -> ListOfJobs:
    async with semaphore:
        try:
            paginator = client.get_paginator('list_objects_v2')
            total_tasks = 0
            total_bytes = 0
            last_modified = None
            mapped_tasks = []

            async for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=job_prefix):
                contents = page.get('Contents', [])
                for batch in contents:
                    if batch['Key'] == job_prefix:
                        continue
                    total_tasks += 1
                    total_bytes += batch['Size']
                    task_date = batch['LastModified']

                    if last_modified is None or task_date > last_modified:
                        last_modified = task_date

                    mapped_tasks.append(_map_keys(batch))

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
                batches=mapped_tasks
            )
        except Exception as e:
            log.error(
                f"Error crítico al procesar metadatos del Job {job_prefix}: {e}")
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
    first_date: datetime.datetime
) -> None:
    """
    Sube el archivo Parquet local a S3 de forma asíncrona y no bloqueante.
    """
    s3_key = (
        f"{settings.s3_prefix_compacted_data}/"
        f"job_id={job_id}/year={first_date.year}/month={first_date.month}/"
        f"day={first_date.day}/part-{part_idx:04d}-{uuid.uuid4().hex[:6]}.parquet"
    )
    log.info(f"Subiendo archivo Parquet compactado Parte {part_idx} a S3: {s3_key}")
    
    # Subida en streaming directo desde disco local para evitar Heap Inflation en Python
    with open(local_path, "rb") as f:
        await client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=s3_key,
            Body=f
        )


async def clear_job(client, job: ListOfJobs) -> None:
    if not job or not job.batches:
        return
    try:
        log.info(
            f"Iniciando purga de la Landing Zone para el Job: {job.prefix}")
        objects_to_delete = [{'Key': batch.key} for batch in job.batches]

        # Eliminación masiva controlada en bloques de 1000 objetos (Límite API de AWS S3)
        for i in range(0, len(objects_to_delete), 1000):
            chunk = objects_to_delete[i:i+1000]
            await client.delete_objects(
                Bucket=settings.s3_bucket_name,
                Delete={'Objects': chunk}
            )
        log.info(
            f"Todos los archivos ({len(objects_to_delete)}) del Job {job.prefix} fueron eliminados.")
    except Exception as e:
        log.error(
            f"Error crítico en la purga del Job: {job.prefix} | Error: {e}")


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


async def process_job(client, job: ListOfJobs, compaction_semaphore: asyncio.Semaphore) -> ListOfJobs | None:
    async with compaction_semaphore:
        if not _is_job_eligible(job):
            log.info(
                f"Ignorando Job {job.prefix} | No cumple umbrales mínimos de tamaño o inactividad.")
            return None

        job_id = job.prefix.rstrip("/").split("/")[-1].split("=")[-1]
        schema = _get_compaction_schema()
        
        part_counter = 0
        rows_in_current_file = 0
        rows_in_chunk = 0
        current_chunk_buffer = {field.name: [] for field in schema}
        
        local_file_path = os.path.join(TMP_DIR, f"compact_{job_id}_{part_counter}.parquet")
        writer = None
        first_date = None

        try:
            log.info(
                f"Iniciando compactación incremental en disco de Job: {job.prefix}")

            for b_idx, batch in enumerate(job.batches):
                response = await client.get_object(Bucket=settings.s3_bucket_name, Key=batch.key)
                stream = response['Body']
                try:
                    async for line in stream.iter_lines():
                        if not line:
                            continue

                        line_data = json.loads(line.decode('utf-8'))
                        parse_result = ParseResult.model_validate(line_data)

                        # Inicializar el escritor de Parquet en el primer registro
                        if writer is None:
                            writer = pq.ParquetWriter(local_file_path, schema=schema, compression='zstd')
                            first_date = parse_result.task.created_at
                            if first_date.tzinfo is None:
                                first_date = first_date.replace(tzinfo=datetime.timezone.utc)

                        current_chunk_buffer["task_id"].append(parse_result.task.task_id)
                        current_chunk_buffer["url"].append(str(parse_result.task.url))
                        current_chunk_buffer["date"].append(parse_result.task.created_at)
                        current_chunk_buffer["data"].append(json.dumps(parse_result.data))

                        rows_in_chunk += 1
                        rows_in_current_file += 1

                        # Al alcanzar el tamaño del micro-búfer, escribimos al disco local
                        if rows_in_chunk >= CHUNK_WRITE_SIZE:
                            await asyncio.to_thread(_write_chunk_to_file, writer, schema, current_chunk_buffer)
                            current_chunk_buffer = {field.name: [] for field in schema}
                            rows_in_chunk = 0

                            # Monitorear tamaño real del archivo en disco
                            if os.path.exists(local_file_path) and os.path.getsize(local_file_path) >= MAX_FILE_SIZE_BYTES:
                                # Calcular bytes remanentes en los lotes crudos de S3
                                batches_restantes = job.batches[b_idx + 1:]
                                bytes_remanentes_s3 = sum(b.size for b in batches_restantes)

                                # Si lo que queda por procesar en S3 es menor al umbral elástico,
                                # evitamos hacer el corte y permitimos que el archivo crezca ligeramente
                                # para no dejar un micro-archivo de Parquet huérfano.
                                if bytes_remanentes_s3 > TOLERANCIA_COLA_BYTES:
                                    writer.close()
                                    writer = None

                                    await _upload_compacted_file(client, local_file_path, job_id, part_counter, first_date)
                                    if os.path.exists(local_file_path):
                                        os.remove(local_file_path)

                                    part_counter += 1
                                    rows_in_current_file = 0
                                    local_file_path = os.path.join(TMP_DIR, f"compact_{job_id}_{part_counter}.parquet")

                finally:
                    stream.close()

            # Escribir registros remanentes del último chunk
            if rows_in_chunk > 0 and writer is not None:
                await asyncio.to_thread(_write_chunk_to_file, writer, schema, current_chunk_buffer)
                current_chunk_buffer = {field.name: [] for field in schema}
                rows_in_chunk = 0

            # Cerrar y subir la parte final si contiene datos
            if writer is not None:
                writer.close()
                writer = None

            if rows_in_current_file > 0:
                await _upload_compacted_file(client, local_file_path, job_id, part_counter, first_date)
                if os.path.exists(local_file_path):
                    os.remove(local_file_path)
                part_counter += 1

            if part_counter == 0:
                log.info(
                    f"Job {job_id} no contenía registros analíticos válidos.")
                if os.path.exists(local_file_path):
                    os.remove(local_file_path)
                return None

            log.info(
                f"Job {job_id} consolidado globalmente de forma exitosa en {part_counter} parte(s).")
            return job

        except Exception as e:
            log.error(
                f"Fallo crítico al compactar Job: {job.prefix} | Error: {e}")
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            if os.path.exists(local_file_path):
                try:
                    os.remove(local_file_path)
                except Exception:
                    pass
            raise


async def main():
    log.info("Iniciando micro-orquestador de compactación S3...")
    client_context = _get_client()

    async with client_context as client:
        try:
            # Fase 1: Descubrimiento de Carpetas Virtuales
            job_prefixes = await get_list_of_jobs(client)
            if not job_prefixes:
                log.info(
                    "No se encontraron particiones de datos en la Landing Zone.")
                return

            # Fase 2: Análisis Concurrente de Metadatos (I/O Bound)
            metadata_semaphore = asyncio.Semaphore(20)
            metadata_tasks = [
                get_list_of_batches(client, prefix, metadata_semaphore)
                for prefix in job_prefixes
            ]

            analyzed_jobs: List[ListOfJobs] = await asyncio.gather(*metadata_tasks, return_exceptions=False)
            log.info(
                f"Análisis finalizado. {len(analyzed_jobs)} Jobs validados.")

            # Fase 3: Procesamiento de Compactación Concurrente Limitada (CPU/RAM Bound)
            compaction_semaphore = asyncio.Semaphore(3)
            compaction_tasks = [
                process_job(client, job, compaction_semaphore)
                for job in analyzed_jobs
            ]

            raw_results = await asyncio.gather(*compaction_tasks, return_exceptions=False)

            # FILTRADO DE COMPROMISO: Descartamos los retornos None (Jobs ignorados)
            jobs_successfully_compacted = [
                job for job in raw_results if job is not None]
            log.info(
                f"Proceso analítico finalizado. {len(jobs_successfully_compacted)} jobs consolidados con éxito.")

            # Fase 4: Purga e Idempotencia del Data Lake
            if jobs_successfully_compacted:
                log.info(
                    f"Procediendo a purgar {len(jobs_successfully_compacted)} carpetas de la Landing Zone...")
                await asyncio.gather(*[clear_job(client, job) for job in jobs_successfully_compacted])

                # Saneamiento de referencias en RAM
                for job in jobs_successfully_compacted:
                    if job in analyzed_jobs:
                        analyzed_jobs.remove(job)

                log.info(
                    "Fase de purga y limpieza de memoria finalizada con éxito.")

        except Exception as e:
            log.error(
                f"Error catastrófico en el proceso de compactación global: {str(e)}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
