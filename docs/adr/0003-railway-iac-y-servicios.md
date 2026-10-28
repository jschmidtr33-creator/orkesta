# ADR 0003: Railway con Infrastructure as Code; web y worker separados

- Estado: aceptada
- Fecha: 2026-10-28

## Contexto

Railway marcó `railway.json` (Config as Code) como obsoleto: los servicios nuevos no pueden
usarlo y los archivos existentes dejan de leerse el 2026-12-01. Su reemplazo es Infrastructure as
Code en `.railway/railway.ts` (TypeScript, disponible de forma general; Python y Go en beta).

## Decisión

- La infraestructura se declara en `.railway/railway.ts` y se aplica con
  `railway config plan` / `railway config apply`.
- Tres recursos: `orkesta-web`, `orkesta-worker` y PostgreSQL. Ambos servicios usan el mismo
  Dockerfile y repositorio; solo cambia el comando de inicio.
- Las migraciones corren como `preDeploy` del servicio web.
- El repositorio se conecta a cada servicio desde el panel, para no escribir en el código
  identificadores de la cuenta.

## Consecuencias

- La configuración de despliegue está versionada y se revisa en pull requests.
- Se necesita Node.js solo para validar y aplicar la configuración, no para la aplicación.
- Separar web y worker evita que un lote largo bloquee la interfaz o se corte al redesplegar la web.
