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

# 3. Copiamos worker a /usr/src/app/worker
COPY worker/ ./worker/

# 4. Entramos al proyecto
WORKDIR /usr/src/app/worker

# 5. Instalamos
RUN uv sync \
    && chown -R appuser:appgroup /usr/src/app
    
USER appuser

ENV PATH="/usr/src/app/worker/.venv/bin:$PATH"
# 5. Ejecución
CMD ["uv", "run", "python", "main.py"]