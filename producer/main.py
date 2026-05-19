from contextlib import asynccontextmanager

from fastapi import APIRouter
from dependencies.dependencies import get_task_producer
from scraping.controller import routesScrapingTasks
from fastapi import FastAPI
from config.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Forzamos la creación y warm-up del cliente SQS y su pool de conexiones al arrancar
    get_task_producer()
    yield
    # Nos aseguramos de cerrar todos los sockets abiertos al apagar la app
    await get_task_producer().close()

app = FastAPI(
    title="Web Scraping Distributed",
    version="1.0",
    description="API para orquestar tareas de web scraping distribuidas",
    lifespan=lifespan
)

# Control de versiones - V1
api_v1_router = APIRouter(prefix="/v1")

# Rutas de la version 1
api_v1_router.include_router(routesScrapingTasks)

# Importación de las rutas por version
app.include_router(api_v1_router)
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app="main:app",
        host=settings.producer_host,
        port=settings.producer_port,
        reload=settings.debug_mode
    )
