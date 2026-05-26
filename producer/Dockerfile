FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1

# 1. Definimos la base
WORKDIR /usr/src/app

RUN groupadd --system appgroup && useradd --system -g appgroup appuser

# 2. Copiamos shared a /usr/src/app/shared
COPY shared/ ./shared/

# 3. Copiamos producer a /usr/src/app/producer
COPY producer/ ./producer/

# 4. Entramos al proyecto
WORKDIR /usr/src/app/producer

# 5. Instalamos
# Eliminamos cualquier rastro de configuración previa
RUN uv sync --frozen --no-dev --no-build \
    && chown -R appuser:appgroup /usr/src/app
    
USER appuser

ENV PATH="/usr/src/app/producer/.venv/bin:$PATH"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]