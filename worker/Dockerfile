FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 1. Definimos la base
WORKDIR /usr/src/app

# 2. Copiamos shared a /usr/src/app/shared
COPY shared/ ./shared/

# 3. Copiamos worker a /usr/src/app/worker
COPY worker/ ./worker/

# 4. Entramos al proyecto
WORKDIR /usr/src/app/worker

# 5. Instalamos
RUN uv sync

ENV PATH="/usr/src/app/worker/.venv/bin:$PATH"
# 5. Ejecución
CMD ["uv", "run", "python", "main.py"]