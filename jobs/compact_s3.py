import asyncio
import datetime
import json
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
MAX_RECORDS_PER_FILE = 1_000_000 
TOLERANCIA_COLA = 200_000  # 20% de elasticidad sobre el bloque óptimo


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
        log.info(f"Iniciando listado de objetos en el bucket: {settings.s3_bucket_name}")

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
                last_modified = last_modified.replace(tzinfo=datetime.timezone.utc)

            return ListOfJobs(
                prefix=job_prefix,
                total_tasks=total_tasks,
                total_bytes=total_bytes,
                last_modified=last_modified,
                inactive_time=now_utc - last_modified,
                batches=mapped_tasks
            )
        except Exception as e:
            log.error(f"Error crítico al procesar metadatos del Job {job_prefix}: {e}")
            raise


def _sync_write_parquet_to_s3(s3_path: str, schema: pa.Schema, buffer_data: Dict[str, List[Any]]) -> None:
    """
    Escribe la tabla columnar directamente en el S3 destino usando el motor C++ de PyArrow.
    """
    s3_fs = S3FileSystem(
        access_key=settings.aws_access_key_id,
        secret_key=settings.aws_secret_access_key,
        region=settings.s3_region,
        endpoint_override=settings.s3_endpoint_url
    )
    table = pa.Table.from_pydict(buffer_data, schema=schema)
    
    with s3_fs.open_output_stream(s3_path) as stream:
        with pq.ParquetWriter(stream, schema=schema, compression='zstd') as writer:
            writer.write_table(table)


async def clear_job(client, job: ListOfJobs) -> None:
    if not job or not job.batches:
        return
    try:
        log.info(f"Iniciando purga de la Landing Zone para el Job: {job.prefix}")
        objects_to_delete = [{'Key': batch.key} for batch in job.batches]
        
        # Eliminación masiva controlada en bloques de 1000 objetos (Límite API de AWS S3)
        for i in range(0, len(objects_to_delete), 1000):
            chunk = objects_to_delete[i:i+1000]
            await client.delete_objects(
                Bucket=settings.s3_bucket_name,
                Delete={'Objects': chunk}
            )
        log.info(f"Todos los archivos ({len(objects_to_delete)}) del Job {job.prefix} fueron eliminados.")
    except Exception as e:        
        log.error(f"Error crítico en la purga del Job: {job.prefix} | Error: {e}")


async def process_job(client, job: ListOfJobs, compaction_semaphore: asyncio.Semaphore) -> ListOfJobs | None:
    async with compaction_semaphore:
        if job.total_bytes < MIN_BYTES_FOR_COMPACTION and job.inactive_time.total_seconds() < MIN_INACTIVITY_SECONDS:
            log.info(f"Ignorando Job {job.prefix} | No cumple umbrales mínimos de tamaño o inactividad.")
            return None

        try:
            log.info(f"Iniciando procesamiento rotativo elástico de Job: {job.prefix}")
            job_id = job.prefix.rstrip("/").split("/")[-1].split("=")[-1]
            
            schema = pa.schema([
                pa.field("task_id", pa.string()),
                pa.field("url", pa.string()),
                pa.field("date", pa.timestamp("us")),
                pa.field("data", pa.string())  # Resiliencia analítica absoluta ante esquemas polimórficos
            ])

            columnar_buffer: Dict[str, List[Any]] = {field.name: [] for field in schema}
            
            part_counter = 0
            rows_in_segment = 0
            bytes_in_segment = 0

            async def _flush_buffer_to_s3(buffer_to_write: dict, part_idx: int) -> None:
                first_date = buffer_to_write["date"][0]
                s3_target_path = (
                    f"{settings.s3_bucket_name}/{settings.s3_prefix_compacted_data}/"
                    f"job_id={job_id}/year={first_date.year}/month={first_date.month}/"
                    f"day={first_date.day}/part-{part_idx:04d}-{uuid.uuid4().hex[:6]}.parquet"
                )
                log.info(f"Enviando streaming Parquet (ZSTD) Parte {part_idx} para el Job: {job_id}")
                await asyncio.to_thread(_sync_write_parquet_to_s3, s3_target_path, schema, buffer_to_write)

            for b_idx, batch in enumerate(job.batches):
                response = await client.get_object(Bucket=settings.s3_bucket_name, Key=batch.key)
                stream = response['Body']
                try:
                    async for line in stream.iter_lines():
                        if not line:
                            continue

                        bytes_in_segment += len(line)
                        line_data = json.loads(line.decode('utf-8'))
                        parse_result = ParseResult.model_validate(line_data)
                        
                        columnar_buffer["task_id"].append(parse_result.task.task_id)
                        columnar_buffer["url"].append(str(parse_result.task.url))
                        columnar_buffer["date"].append(parse_result.task.created_at)
                        columnar_buffer["data"].append(json.dumps(parse_result.data))

                        rows_in_segment += 1

                        # PUNTO DE DECISIÓN CRÍTICO: ELASTIC CHUNKING LOOK-AHEAD
                        if rows_in_segment == MAX_RECORDS_PER_FILE:
                            batches_restantes = job.batches[b_idx + 1:]
                            bytes_remanentes_s3 = sum(b.size for b in batches_restantes)
                            
                            bytes_por_registro = bytes_in_segment / rows_in_segment
                            filas_estimadas_restantes = bytes_remanentes_s3 / bytes_por_registro if bytes_por_registro > 0 else 0
                            
                            if filas_estimadas_restantes <= TOLERANCIA_COLA:
                                # Zona Elástica Detectada: Prohibimos el corte, permitimos expandir RAM temporalmente
                                log.info(
                                    f"Look-Ahead en {job_id}: Cola estimada de {filas_estimadas_restantes:.0f} filas "
                                    f"({bytes_remanentes_s3 / 1024 / 1024:.2f} MB) entra en la tolerancia del 20%. Absorbiendo cola..."
                                )
                            else:
                                # La cola es legítimamente grande: Corte e inyección inmediata a la red S3
                                await _flush_buffer_to_s3(columnar_buffer, part_counter)
                                columnar_buffer = {field.name: [] for field in schema}
                                part_counter += 1
                                rows_in_segment = 0
                                bytes_in_segment = 0
                finally:
                    stream.close()

            # CIERRE DE PROCESO: Guardar remanentes o colas absorbidas elásticamente
            if columnar_buffer["task_id"]:
                await _flush_buffer_to_s3(columnar_buffer, part_counter)
                part_counter += 1
            
            del columnar_buffer

            if part_counter == 0:
                log.info(f"Job {job_id} no contenía registros analíticos válidos.")
                return None

            log.info(f"Job {job_id} consolidado globalmente de forma exitosa en {part_counter} parte(s).")
            return job

        except Exception as e:
            log.error(f"Fallo crítico al compactar Job: {job.prefix} | Error: {e}")
            raise


async def main():
    log.info("Iniciando micro-orquestador de compactación S3...")
    client_context = _get_client()

    async with client_context as client:
        try:
            # Fase 1: Descubrimiento de Carpetas Virtuales
            job_prefixes = await get_list_of_jobs(client)
            if not job_prefixes:
                log.info("No se encontraron particiones de datos en la Landing Zone.")
                return

            # Fase 2: Análisis Concurrente de Metadatos (I/O Bound)
            metadata_semaphore = asyncio.Semaphore(20)  
            metadata_tasks = [
                get_list_of_batches(client, prefix, metadata_semaphore) 
                for prefix in job_prefixes
            ]
            
            analyzed_jobs: List[ListOfJobs] = await asyncio.gather(*metadata_tasks, return_exceptions=False)
            log.info(f"Análisis finalizado. {len(analyzed_jobs)} Jobs validados.")

            # Fase 3: Procesamiento de Compactación Concurrente Limitada (CPU/RAM Bound)
            compaction_semaphore = asyncio.Semaphore(3)  
            compaction_tasks = [
                process_job(client, job, compaction_semaphore) 
                for job in analyzed_jobs
            ]
            
            raw_results = await asyncio.gather(*compaction_tasks, return_exceptions=False)
            
            # FILTRADO DE COMPROMISO: Descartamos los retornos None (Jobs ignorados)
            jobs_successfully_compacted = [job for job in raw_results if job is not None]
            log.info(f"Proceso analítico finalizado. {len(jobs_successfully_compacted)} jobs consolidados con éxito.")

            # Fase 4: Purga e Idempotencia del Data Lake
            if jobs_successfully_compacted:
                log.info(f"Procediendo a purgar {len(jobs_successfully_compacted)} carpetas de la Landing Zone...")
                await asyncio.gather(*[clear_job(client, job) for job in jobs_successfully_compacted])

                # Saneamiento de referencias en RAM
                for job in jobs_successfully_compacted:
                    if job in analyzed_jobs:
                        analyzed_jobs.remove(job)

                log.info("Fase de purga y limpieza de memoria finalizada con éxito.")
                
        except Exception as e:
            log.error(f"Error catastrófico en el proceso de compactación global: {str(e)}")
            raise


if __name__ == "__main__":
    asyncio.run(main())