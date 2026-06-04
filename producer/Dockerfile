FROM python:3.13-slim

# Copiamos uv directamente desde la imagen oficial de Rust/Astral
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Optimizaciones de entorno de producción para Python y uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1 \
    UV_CACHE_DIR="/tmp/.uv-cache"

WORKDIR /app

# =========================================================
# FASE 1: Mapeo del Workspace y Caché de Internet
# =========================================================

# 1. Copiamos ÚNICAMENTE los archivos de configuración de todo el Monorepo
# Esto le permite a uv leer el candado global (uv.lock) sin ver el código fuente todavía
COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml ./shared/
COPY producer/pyproject.toml ./producer/
COPY worker/pyproject.toml ./worker/

# 2. INSTALACIÓN ESTRICTA (Luz verde en SonarQube)
# Añadimos --mount=type=cache para que la caché de root no se herede en la imagen final
RUN --mount=type=cache,target=/tmp/.uv-cache \
    uv sync --frozen --no-build --no-install-workspace

# =========================================================
# FASE 2: Inyección de Código y Ejecución
# =========================================================

# 3. Copiamos el código fuente de los módulos necesarios para este servicio
COPY shared/ ./shared/
COPY producer/ ./producer/

# 4. Securización del contenedor con un usuario sin privilegios
RUN groupadd --system appgroup && useradd --system -g appgroup appuser \
    && chown -R appuser:appgroup /app

USER appuser

# Enlace de arquitectura: Permite que el worker localice la librería shared
ENV PYTHONPATH="/app/shared"

WORKDIR /app/producer

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]