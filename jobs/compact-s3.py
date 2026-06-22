import asyncio
import datetime
import json
from typing import List

import aioboto3
from botocore.config import Config
from pydantic import BaseModel
from shared.logging import Logger

from config.settings import settings

log = Logger("Compactar S3")


class S3BatchFile(BaseModel):
    key: str
    size: int
    last_modified: datetime.datetime


class ListOfJobs(BaseModel):
    prefix: str
    total_tasks: int
    total_bytes: int
    last_modified: datetime.datetime
    inactive_time: datetime.timedelta
    batches: List[S3BatchFile]


async def _get_client():
    config = Config(
        max_pool_connections=50,
        retries={'max_attempts': 5, 'mode': 'standard'}
    )
    session = aioboto3.Session()
    try:
        client_context = session.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=config
        )
        return await client_context.__aenter__()
    except Exception as e:
        log.error(f"Fallo crítico al conectar con S3: {e}")
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
        async for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=settings.s3_prefix_raw_data + "/", Delimiter="/"):
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
                    total_tasks += 1
                    total_bytes += batch['Size']
                    task_date = batch['LastModified']

                    if last_modified is None or task_date > last_modified:
                        last_modified = task_date

                    mapped_tasks.append(_map_keys(batch))

            # Manejo de borde: carpeta virtual vacía en S3
            if last_modified is None:
                last_modified = datetime.datetime.now(datetime.timezone.utc)

            # Sincronizamos marcas de tiempo timezone-aware a UTC puro para el cálculo delta
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


async def process_job(client, job: ListOfJobs):
    #  Valores límite expresados de forma clara
    MIN_BYTES_FOR_COMPACTION = 150 * 1024 * 1024  # 150 MB
    MIN_INACTIVITY_SECONDS = 60 * 30             # 30 Minutos

    if job.total_bytes < MIN_BYTES_FOR_COMPACTION and job.inactive_time.total_seconds() < MIN_INACTIVITY_SECONDS:
        log.info(
            f"Ignorando Job {job.prefix} | Tamaño: {job.total_bytes / 1024:.2f} KB | Inactividad: {job.inactive_time.total_seconds():.0f}s. No cumple umbrales.")
        return

    try:
        log.info(f"Iniciando descarga de Job: {job.prefix}")
        registros = []

        for batch in job.batches:
            response = await client.get_object(Bucket=settings.s3_bucket_name, Key=batch.key)

            async for line in response['Body'].iter_lines():
                if not line:
                    continue

                registro = json.loads(line.decode('utf-8'))
                registros.push(registro)

            return registros
    except Exception as e:
        log.error(f"Fallo crítico al descargar Job: {job.prefix} | Error: {e}")


async def main():
    log.info("Iniciando compactación de S3...")
    client = await _get_client()

    try:
        # 1. Descubrimiento de carpetas de Jobs (Fase 1)
        job_prefixes = await get_list_of_jobs(client)
        if not job_prefixes:
            log.info("No se encontraron particiones de datos en la Landing Zone.")
            return

        # 2. Análisis Concurrente Controlado de Metadatos (Fase 2)
        semaphore = asyncio.Semaphore(20)  # Evita el Throttling HTTP 503 de S3
        metadata_tasks = [get_list_of_batches(
            client, prefix, semaphore) for prefix in job_prefixes]
        
        analyzed_jobs = await asyncio.gather(*metadata_tasks, return_exceptions=False)

        log.info(
            f"Análisis de metadatos finalizado con éxito para {len(analyzed_jobs)}/{len(job_prefixes)} jobs.")



    finally:
        # Garantizamos el cierre de conexiones bajo cualquier excepción
        await client.__aexit__(None, None, None)
        log.info("Proceso de compactación finalizado y canales de red cerrados.")


if __name__ == "__main__":
    asyncio.run(main())
