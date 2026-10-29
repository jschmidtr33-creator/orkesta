# ORKESTA

Plataforma de prospección B2B y enriquecimiento de datos con motor de machine learning propio.
A partir de un CSV con **Nombre, Apellido, Cargo, Empresa y Ubicación**, ORKESTA identifica el
dominio corporativo, detecta el patrón de correo de la empresa y propone el correo de cada
persona, con evidencia, nivel de confianza y etiqueta (`publicly_found` o `inferred`).

No usa Gemini, OpenAI ni Google Search API. El código corre en Railway y vive en GitHub.

## Estado

| Etapa | Contenido | Estado |
| --- | --- | --- |
| 0 | Propuesta arquitectónica | Aprobada |
| 1 | Base ejecutable: API, PostgreSQL, migraciones, worker, CI, Railway | Hecha |
| **2** | **Acceso, importación CSV/XLSX, mapeo, normalización, deduplicación, exportación** | **Esta entrega** |
| 3 | Motor: señales, DNS/MX, evidencia, modelos ML de dominio y patrón | Pendiente |
| 4 | Evaluación con conjunto etiquetado | Pendiente |
| 5 | Interfaz operativa | Pendiente |
| 6 | Campañas Gmail | Pendiente |
| 7 | SaaS | Pendiente |

Lo que funciona hoy:

1. **Acceso** con correo y contraseña (Argon2id), sesión firmada, protección CSRF y bloqueo
   temporal tras 5 intentos fallidos.
2. **Importación** de CSV (UTF-8, Windows-1252, Latin-1; coma, punto y coma, tabulador) y XLSX,
   hasta 10 MB y 50.000 filas por lote.
3. **Mapeo de columnas** propuesto automáticamente (español e inglés) y editable, con vista
   previa del resultado antes de confirmar. Acepta Nombre y Apellido por separado o un
   **Nombre completo** en una sola columna (exportaciones de LinkedIn): ORKESTA separa nombres
   y apellidos, retira textos pegados ("- BLINDAJE AUTOMOTRIZ"), iniciales intermedias y
   apodos, y avisa cuando el apellido solo trae la inicial.
4. **Normalización:** nombres compuestos, dos apellidos, partículas ("de la"), tratamientos
   ("Ing.", "Dra.") y credenciales; formas jurídicas de LATAM (S.A.S., S.A.B. de C.V., Ltda.,
   BIC, E.S.P.); ubicaciones libres a país ISO y ciudad con nivel de confianza.
5. **Validación y deduplicación:** errores (falta nombre, empresa no válida como "Freelance")
   y avisos (país no reconocido, ciudad ambigua); duplicados dentro del archivo y contactos que
   ya existían de lotes anteriores.
6. **Exportación** CSV y XLSX con las columnas originales intactas más las de ORKESTA, con
   protección contra inyección de fórmulas.
7. **Borrado de lotes** con sus contactos exclusivos, y registro de auditoría de cada acción.

La importación corre en el worker: la vista previa de 50.000 filas tarda unos 3 s y la
importación completa unos 25 s en segundo plano.

## Estructura

```
src/orkesta/
  config.py            Ajustes desde variables de entorno
  logs.py              Logs JSON a stdout
  auth.py              Usuarios, Argon2id, límite de intentos de acceso
  audit.py             Registro de auditoría
  cli.py               create-admin y bootstrap-admin
  api/main.py          App FastAPI: /health, /ready, sesión, cabeceras de seguridad
  web/                 Interfaz: rutas, plantillas Jinja2 y CSS
  engine/normalize/    Normalización de nombres, empresas y ubicaciones
  imports/             Lectura, mapeo, validación, servicio, exportación y trabajo del worker
  db/base.py           Base declarativa, UUID, timestamps, organization_id
  db/models.py         Las 16 tablas del MVP
  db/session.py        Motor y sesiones
  db/migrations_state.py  Estado de migraciones para /ready
  migrations/          Alembic (env.py y versiones)
  worker/queue.py      Cola: encolar, reclamar, completar, fallar, reanudar, cancelar
  worker/runner.py     Bucle del worker y manejo de errores
  worker/__main__.py   Entrada: python -m orkesta.worker
tests/                 Pruebas con PostgreSQL real
.railway/railway.ts    Infraestructura en Railway (web, worker, PostgreSQL)
.github/workflows/ci.yml  CI: formato, lint, tipos, pruebas, auditoría de dependencias
docs/adr/              Registro de decisiones de arquitectura
```

## Desarrollo local

Requisitos: Python 3.12, PostgreSQL 16 o superior.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install -e ".[dev]"

cp .env.example .env            # ajusta DATABASE_URL si hace falta
createdb orkesta_dev
alembic upgrade head

python -m orkesta.cli create-admin --email tu@correo.com --org-name "America Digital"
uvicorn --factory orkesta.api.main:create_app --reload   # http://localhost:8000
python -m orkesta.worker                                 # en otra terminal: procesa las importaciones
```

### Pruebas y controles

```bash
export TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres
pytest                       # crea y borra una base temporal por ejecución
ruff format --check src tests
ruff check src tests
mypy
pip-audit -r requirements.txt --require-hashes --disable-pip
```

Sin `TEST_DATABASE_URL`, las pruebas que necesitan base de datos se omiten.

### Migraciones

```bash
alembic revision --autogenerate -m "descripción"   # tras cambiar db/models.py
alembic upgrade head
alembic downgrade -1
alembic check                                      # falla si modelos y migraciones difieren
```

Revisa siempre el archivo generado en `src/orkesta/migrations/versions/` antes de subirlo.

### Dependencias

`pyproject.toml` acota las versiones; `requirements.txt` las fija con hashes. Para actualizar:

```bash
uv pip compile pyproject.toml --python-version 3.12 --generate-hashes -o requirements.txt
```

## Despliegue

### 1. Repositorio en GitHub

```bash
git init -b main
git add .
git commit -m "Etapa 1: base ejecutable"
gh repo create orkesta --private --source . --push
```

El flujo de trabajo es: rama `feature/...` → pull request → CI en verde → merge a `main`.
En GitHub, activa la protección de `main` (Settings → Branches) exigiendo que pase el CI.

### 2. Proyecto en Railway

Requiere el CLI de Railway 5.42.1 o superior (`npm i -g @railway/cli`).

```bash
railway login
railway init --name orkesta       # o `railway link` si el proyecto ya existe
cd .railway && npm install && cd ..
railway config plan               # revisa los cambios que se aplicarán
railway config apply              # crea PostgreSQL, orkesta-web y orkesta-worker
```

Después, en el panel de Railway:

1. En **orkesta-web** y **orkesta-worker**: Settings → Source → conecta el repositorio de
   GitHub, rama `main`. Railway detecta el `Dockerfile` y lo usa para construir.
2. En ambos servicios, Variables → crea `SECRET_KEY` con un valor generado por
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. En **orkesta-web**, Variables → crea `ORKESTA_BOOTSTRAP_ADMIN_EMAIL` y
   `ORKESTA_BOOTSTRAP_ADMIN_PASSWORD` (mínimo 12 caracteres). En el primer despliegue se crea
   ese administrador; en los siguientes no hace nada. Después puedes borrar la contraseña.
4. En **orkesta-web**: Settings → Networking → Generate Domain para obtener la URL pública.
5. En **PostgreSQL**: activa los backups automáticos si tu plan los incluye y no le des
   acceso de red público.
6. Recursos: para lotes de 50.000 filas, el worker necesita unos 512 MB de memoria.

### 3. Verificación

```bash
curl https://<tu-dominio>.up.railway.app/health   # {"status":"ok","version":"0.1.0"}
curl https://<tu-dominio>.up.railway.app/ready    # {"status":"ready",...}
```

`/ready` responde 503 si la base no es accesible o si faltan migraciones. Abre el dominio en el
navegador e inicia sesión con el administrador del paso 3.

### Operación

- **Logs:** `railway logs --service orkesta-web` o la pestaña Deployments → Logs. Son JSON.
- **Migraciones:** corren solas antes de cada despliegue de la web (`preDeploy`). Si fallan, el
  despliegue se detiene y la versión anterior sigue activa.
- **Rollback:** Deployments → versión anterior → Redeploy. Las migraciones no se revierten
  solas: si una versión nueva cambió el esquema, ejecuta `alembic downgrade` antes del rollback.
- **Actualizar sin perder datos:** los datos viven en PostgreSQL, no en el contenedor. Un nuevo
  despliegue reemplaza solo el código.
- **Respaldo manual:** `railway connect postgres` abre psql; para un volcado,
  `pg_dump "$DATABASE_URL" > backup.sql` desde una máquina con acceso, guardado fuera del repo.

## Seguridad

- Ningún secreto en el repositorio: `.env` y los planes de Railway están en `.gitignore`.
- Archivos de contactos (`*.csv`, `*.xlsx`) excluidos del repositorio, salvo fixtures de prueba.
- `SECRET_KEY` de al menos 32 caracteres obligatoria en staging y producción. Firma las
  sesiones: si cambia, todos los usuarios deben volver a iniciar sesión.
- Contraseñas con Argon2id; bloqueo de 15 minutos tras 5 intentos fallidos por correo e IP
  (contador en memoria: válido mientras haya una sola réplica web).
- Cookie de sesión `HttpOnly`, `SameSite=Lax` y `Secure` en producción; token CSRF en todos los
  formularios; cabeceras CSP, `X-Frame-Options` y HSTS.
- Cada lote, fila y exportación se filtra por organización.
- Los logs y la auditoría no guardan datos de contactos.
- El contenedor corre con un usuario sin privilegios.
