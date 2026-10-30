# ORKESTA: una sola imagen con la API, la app web (/app) y el sitio público (/).

# 1) Frontend: app React y sitio Astro, compilados a apps/api/orkesta/static.
FROM node:22-slim AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
COPY packages/ui/package.json packages/ui/
COPY apps/web/package.json apps/web/
COPY apps/site/package.json apps/site/
RUN npm ci --no-audit --no-fund
COPY packages packages
COPY apps/web apps/web
COPY apps/site apps/site
RUN mkdir -p apps/api/orkesta/static && npm run build

# 2) Backend Python con los archivos estáticos ya compilados.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app
WORKDIR /app
COPY apps/api/requirements.txt ./
RUN pip install -r requirements.txt
COPY apps/api/ ./
COPY --from=frontend /src/apps/api/orkesta/static ./orkesta/static
RUN useradd --create-home --uid 10001 orkesta
USER orkesta
EXPOSE 8000
CMD ["sh", "-c", "uvicorn --factory orkesta.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
