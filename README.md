# ORKESTA

**Conecta. Enriquece. Conquista.** Plataforma SaaS B2B que integra prospección, enriquecimiento de datos, validación de correos, campañas con seguimientos, CRM y propuestas comerciales en un solo sistema multiempresa.

La propuesta técnica completa (análisis, arquitectura, tecnología, estructura y plan por etapas) está en [docs/architecture/README.md](docs/architecture/README.md).

## Estado

| Etapa | Contenido | Estado |
| --- | --- | --- |
| 0 | Fundaciones: multiempresa con aislamiento RLS, registro, sesiones, roles, invitaciones, auditoría, sistema de diseño, app y sitio público | Entregada |
| 1 | Datos comerciales: importación Excel/CSV con mapeo de columnas, deduplicación, procedencia por dato, contactos, listas y exportación | Entregada |
| 2 | Prospección con IA: filtros → agente que investiga empresas y decisores en Wikidata, sitios oficiales y noticias (GDELT), con fuente por dato y guardado en Contactos | Entregada |
| 3 | Validación de correos: formato, dominio/MX, desechables, buzón por SMTP con detección catch-all; búsqueda de correos faltantes y corrección de estimados | Esta entrega |
| 4–6 | Enriquecimiento, campañas, CRM | MVP |
| 7–10 | Propuestas, inteligencia comercial, analítica, SaaS comercial | Después del MVP |

La versión anterior de ORKESTA quedó guardada en la rama `legado-v0`.

## Estructura

```text
apps/api      Backend FastAPI (API, migraciones, pruebas). Sirve también la app y el sitio.
apps/web      App React + Vite + TypeScript, publicada en /app
apps/site     Sitio público Astro, publicado en /
packages/ui   Sistema de diseño: colores, tipografía Plus Jakarta Sans y logo
docs/         Arquitectura y decisiones
```

Una sola imagen Docker sirve todo desde el mismo origen: las cookies de sesión son de primera parte y no hay CORS.

## Desarrollo local

Requisitos: Python 3.12, Node 22 y PostgreSQL 16.

```bash
# Backend
python3.12 -m venv .venv && .venv/bin/pip install -r apps/api/requirements-dev.txt
cp .env.example apps/api/.env          # ajusta DATABASE_URL
cd apps/api && PYTHONPATH=. ../../.venv/bin/python -m orkesta.cli release
PYTHONPATH=. ../../.venv/bin/uvicorn --factory orkesta.main:create_app --reload

# Frontend (otra terminal, desde la raíz)
npm install
npm run dev -w @orkesta/web            # http://localhost:5173/app (redirige /api al backend)
npm run build                          # compila app y sitio hacia apps/api/orkesta/static
```

Pruebas del backend (usan un rol sin privilegios para que Row-Level Security se aplique de verdad):

```bash
cd apps/api && PYTHONPATH=. ../../.venv/bin/pytest
```

## Seguridad

- Cada tabla de empresa lleva `tenant_id` y PostgreSQL aplica **Row-Level Security** (`FORCE`), incluso para el dueño de las tablas.
- Contraseñas con Argon2; acceso con JWT de 15 minutos en cookie `httpOnly`; refresco rotativo con detección de reutilización.
- Límite de intentos de ingreso, verificación de origen en escrituras (CSRF), CSP estricta para scripts y auditoría de acciones.
- Sin secretos en el repositorio: todo por variables de entorno (ver `.env.example`).

## Despliegue

Render con `render.yaml` (Blueprint): base PostgreSQL y un servicio web. Antes de cada despliegue corre `python -m orkesta.cli release` (migraciones y administrador inicial). Variables a completar en Render: `ORKESTA_BOOTSTRAP_ADMIN_EMAIL`, `ORKESTA_BOOTSTRAP_ADMIN_PASSWORD` y, para entrar con Google, `PUBLIC_BASE_URL`, `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`.
