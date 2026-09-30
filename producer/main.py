import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, FastAPI
from dependencies.dependencies import (
    get_task_producer,
    get_db_connection_manager,
    get_domain_policy_repository,
    get_domain_policy_service,
)
from scraping.controller import routesScrapingTasks
from scraping.domain_controller import domain_router
from config.settings import settings

logger = logging.getLogger(__name__)


async def _background_db_health_and_sync(interval_seconds: float = 30.0):
    """
    Corrutina de fondo que asegura la auto-reconexion a PostgreSQL si arranca tarde
    o tras caidas temporales, y mantiene la memoria RAM L1 sincronizada periodicamente.
    """
    db_manager = get_db_connection_manager()
    repo = get_domain_policy_repository()
    service = get_domain_policy_service()

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            if db_manager.pool is None:
                pool = await db_manager.connect(max_retries=1)
                if pool:
                    repo.set_pool(pool)
                    await service.sync_from_db()
                    logger.info("Auto-reconexion exitosa a PostgreSQL. Politicas sincronizadas en RAM L1.")
            else:
                # Sincronizacion periodica para capturar inserciones directas en BD
                await service.sync_from_db()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Error en ciclo de background sync con PostgreSQL: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Warm-up del cliente SQS y su pool de conexiones
    get_task_producer()

    # 2. Conectar a PostgreSQL y sincronizar politicas de dominio con la cache L1
    db_manager = get_db_connection_manager()
    pool = await db_manager.connect()
    if pool:
        get_domain_policy_repository().set_pool(pool)
        await get_domain_policy_service().sync_from_db()

    # 3. Lanzar tarea de fondo para auto-reconexion y sincronizacion periodica
    sync_task = asyncio.create_task(_background_db_health_and_sync(interval_seconds=30.0))

    yield

    # 4. Cierre ordenado de conexiones y tareas de fondo al apagar la app
    sync_task.cancel()
    with suppress(asyncio.CancelledError):
        await sync_task

    await get_task_producer().close()
    await db_manager.close()


app = FastAPI(
    title="Web Scraping Distributed",
    version="1.0",
    description="API para orquestar tareas de web scraping distribuidas con gobernanza de dominios",
    lifespan=lifespan,
)

# Control de versiones - V1
api_v1_router = APIRouter(prefix="/v1")

# Rutas de la version 1
api_v1_router.include_router(routesScrapingTasks)
api_v1_router.include_router(domain_router)

# Importación de las rutas por version
app.include_router(api_v1_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app="main:app",
        host=settings.producer_host,
        port=settings.producer_port,
        reload=settings.debug_mode,
    )
