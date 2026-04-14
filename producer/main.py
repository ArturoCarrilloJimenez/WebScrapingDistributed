from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.settings import settings
from scraping.controller import routesScrapingTasks

# Conditionally disable docs in production
app = FastAPI(
    title="Web Scraping Distributed",
    version="1.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
)


# --- API Key Authentication Middleware ---
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates the X-API-Key header on all requests when API_KEY is configured."""

    EXCLUDED_PATHS = {"/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        if not settings.api_key:
            return await call_next(request)

        if request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if api_key != settings.api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["X-API-Key", "Content-Type"],
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
        host="0.0.0.0",
        port=settings.producer_port,
        reload=settings.debug,
    )
