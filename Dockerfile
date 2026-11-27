# syntax=docker/dockerfile:1.7
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

# 2) Modelo de comprensión de lenguaje de Concertino (código abierto, ~118 MB, corre local).
#    paraphrase-multilingual-MiniLM-L12-v2 cuantizado a 8 bits: entiende el significado de las
#    preguntas en español. Si la descarga falla, la imagen se arma igual y Concertino sigue con
#    sus reglas (lo dice /api/health).
FROM python:3.12-slim AS model
ARG MODEL_REPO=Xenova/paraphrase-multilingual-MiniLM-L12-v2
ARG MODEL_REV=main
RUN pip install --no-cache-dir -q onnx==1.19.1 wordfreq==3.1.1 tokenizers==0.23.3 numpy==2.5.5
COPY apps/api/scripts/podar_modelo.py /tmp/podar_modelo.py
COPY apps/api/orkesta/concertino /tmp/textos
RUN mkdir -p /models/concertino && cd /models/concertino && python - <<'PY'
import hashlib, os, urllib.request
repo, rev = os.environ.get("MODEL_REPO", "Xenova/paraphrase-multilingual-MiniLM-L12-v2"), os.environ.get("MODEL_REV", "main")
files = {"model.onnx": "onnx/model_quantized.onnx", "tokenizer.json": "tokenizer.json"}
try:
    for name, path in files.items():
        url = f"https://huggingface.co/{repo}/resolve/{rev}/{path}"
        urllib.request.urlretrieve(url, name)
        data = open(name, "rb").read()
        print(f"{name}: {len(data) / 1e6:.1f} MB sha256={hashlib.sha256(data).hexdigest()}")
    # Vocabulario solo en español: la RAM del modelo baja de ~480 MB a menos de 300 MB
    import subprocess
    subprocess.run(["python", "/tmp/podar_modelo.py", ".", "/tmp/textos"], check=True)
except Exception as exc:
    print("Modelo de Concertino no descargado:", exc)
    for name in files:
        if os.path.exists(name):
            os.remove(name)
PY

# 3) Backend Python con los archivos estáticos ya compilados.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app
WORKDIR /app
COPY apps/api/requirements.txt ./
RUN pip install -r requirements.txt
COPY apps/api/ ./
COPY --from=frontend /src/apps/api/orkesta/static ./orkesta/static
COPY --from=model /models /opt/models
RUN useradd --create-home --uid 10001 orkesta
USER orkesta
EXPOSE 8000
CMD ["sh", "-c", "uvicorn --factory orkesta.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
