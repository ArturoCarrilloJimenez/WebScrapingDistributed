from contextlib import asynccontextmanager

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

    yield

    # 3. Cierre ordenado de conexiones al apagar la app
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
