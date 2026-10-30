# Arquitectura de ORKESTA

La propuesta técnica aprobada (Fases 1 a 5) vive como documento colaborativo:
**ORKESTA · Propuesta técnica (Fases 1–5)**, https://claude.ai/artifact/3k174zoJCGddYJ8qgMjK2r

## Decisiones vigentes (ADR resumidas)

| # | Decisión | Motivo |
| --- | --- | --- |
| 1 | Monolito modular en Python (FastAPI) | Un lenguaje para API, workers y ML; módulos aislados que pueden separarse después |
| 2 | PostgreSQL con Row-Level Security por empresa | Doble barrera de aislamiento multiempresa |
| 3 | Esquema `orkesta` propio | La versión anterior queda intacta en `public` dentro de la misma base |
| 4 | App React servida en `/app` y sitio Astro en `/` por el mismo servicio | Cookies de primera parte, sin CORS, un solo servicio que pagar |
| 5 | Sin APIs pagas en la primera etapa | Fuentes gratuitas para el motor de investigación (GDELT, Common Crawl, Wikidata, sitios) |
| 6 | Inicio de sesión con Google solo con permisos básicos | No exige verificación de Google; el permiso de envío llega en la etapa de campañas |
| 7 | Sin logos de terceros ni cifras ficticias | Requisito de producto: solo datos reales |
| 8 | Invitaciones por enlace copiable | No hay servicio de correo pagado en la Etapa 0 |
| 9 | Cola de trabajos propia sobre PostgreSQL (`FOR UPDATE SKIP LOCKED`) en lugar de Procrastinate | Sin dependencias extra; los workers corren dentro del servicio web (`RUN_WORKER=true`) para no pagar un segundo servicio |
| 10 | Motor de normalización en `apps/api/orkesta/engine` | Se porta el motor probado de la versión anterior dentro del backend en vez de un paquete aparte |
| 11 | De las bases propias solo se guardan 8 campos | Nombre, apellido, ubicación, teléfono, correo, cargo, empresa e industria; cada valor guarda su fuente |
| 12 | La prospección no depende de bases subidas | El agente parte de filtros y consulta fuentes abiertas; la base propia solo suma empresas de referencia |
| 13 | Fuentes gratuitas: Wikidata (SPARQL), sitios oficiales (respetando robots.txt) y GDELT | Sin presupuesto para APIs; LinkedIn no se rastrea: solo se enlaza el perfil público que Wikidata publica |
| 14 | Correos «publicados» vs «estimados» | El patrón de la empresa se deduce solo de correos publicados en su sitio; los estimados quedan marcados hasta la validación (Etapa 3) |
| 15 | Descargas con protección SSRF y caché global `web_cache` | El usuario puede escribir dominios; solo se permiten IP públicas y las respuestas públicas se reutilizan |
| 16 | Validación propia por DNS y SMTP (sin APIs) | Sin enviar correos: `RCPT TO` con prueba de dirección aleatoria para detectar catch-all. Si la red bloquea el puerto 25 el resultado queda «Sin confirmar», nunca «Válido». Se puede apagar con `SMTP_PROBE=false` |
| 17 | Buscar correos faltantes solo con confirmación del servidor | Se prueban formatos habituales y se guarda únicamente el que el servidor confirma; en dominios catch-all no se adivina |
| 18 | Búsqueda web con IA: Gemini + Google Search (plan gratuito) | Wikidata y los sitios no bastan para LATAM. Una persona solo se acepta si su línea está respaldada por resultados de Google (`groundingSupports`) y se cita la fuente; sin respaldo, se descarta |
