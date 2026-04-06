FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 1. Definimos la base
WORKDIR /usr/src/app

# 2. Copiamos shared a /usr/src/app/shared
COPY shared/ ./shared/

# 3. Copiamos producer a /usr/src/app/producer
COPY producer/ ./producer/

# 4. Entramos al proyecto
WORKDIR /usr/src/app/producer

# 5. Instalamos
# Eliminamos cualquier rastro de configuración previa
RUN uv sync

ENV PATH="/usr/src/app/producer/.venv/bin:$PATH"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]