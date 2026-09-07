FROM python:3.13-slim

# Copiamos uv directamente desde la imagen oficial de Rust/Astral
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Optimizaciones de entorno de producción para Python y uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1 \
    UV_CACHE_DIR="/tmp/.uv-cache" \
    PLAYWRIGHT_BROWSERS_PATH="/app/.playwright-browsers"

WORKDIR /app

# =========================================================
# FASE 1: Mapeo del Workspace y Caché de Internet
# =========================================================

COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml ./shared/
COPY producer/pyproject.toml ./producer/
COPY worker/pyproject.toml ./worker/

RUN --mount=type=cache,target=/tmp/.uv-cache \
    uv sync --frozen --no-build --no-install-workspace --package worker

# =========================================================
# FASE 2: Inyección de Código y Ejecución
# =========================================================

COPY shared/ ./shared/
COPY worker/ ./worker/

# Instalar navegadores de Playwright y dependencias del sistema para Linux
RUN /app/.venv/bin/playwright install --with-deps chromium

RUN groupadd --system appgroup && useradd --system -g appgroup appuser \
    && chown -R appuser:appgroup /app

USER appuser

# Enlace de arquitectura: Permite que el worker localice la librería shared
ENV PYTHONPATH="/app/shared"

# CORRECCIÓN DE CONTEXTO: Nos movemos a la carpeta real del servicio
WORKDIR /app/worker

# Ejecución
CMD ["/app/.venv/bin/python", "main.py"]