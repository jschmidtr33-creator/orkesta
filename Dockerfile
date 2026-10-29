# Imagen única para los servicios web y worker de ORKESTA.
# Railway usa este Dockerfile automáticamente; cada servicio define su propio comando de inicio.

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias fijadas con hashes: la imagen se reconstruye igual en cada despliegue.
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

# El código se usa desde /app/src (PYTHONPATH): sin paso de build ni dependencias extra.
COPY alembic.ini ./
COPY src ./src
RUN useradd --create-home --uid 10001 orkesta

USER orkesta

EXPOSE 8000

# Comando por defecto: servicio web. El worker lo sobrescribe con `python -m orkesta.worker`.
CMD ["sh", "-c", "uvicorn --factory orkesta.api.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
