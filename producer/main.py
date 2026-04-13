from fastapi import APIRouter
from scraping.controller import routesScrapingTasks
from fastapi import FastAPI
from config.settings import settings

app = FastAPI(
    title="Web Scraping Distributed",
    version="1.0",
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
        app="main:app", host="0.0.0.0", port=settings.producer_port, reload=True
    )
