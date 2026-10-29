# ADR 0004: Interfaz servida por FastAPI con plantillas, sin JavaScript

- Estado: aceptada
- Fecha: 2026-10-29

## Contexto

El MVP necesita que una persona no técnica suba bases, revise el mapeo y exporte resultados,
sin mantener un segundo proyecto de frontend ni un segundo despliegue.

## Decisión

- Páginas HTML renderizadas en el servidor con Jinja2, formularios clásicos y CSS propio.
- Sin JavaScript: la política CSP solo permite recursos del propio dominio. La página de un lote
  en importación se actualiza con `<meta http-equiv="refresh">`.
- Sesión en cookie firmada con `SECRET_KEY`, token CSRF en cada formulario y Argon2id para
  contraseñas.
- La vista previa usa exactamente la misma función que la importación definitiva, de modo que
  lo que se ve antes de confirmar es lo que se guarda.

## Consecuencias

- Un solo despliegue y una superficie de ataque pequeña.
- La interacción es más básica que en una aplicación de una sola página (cambiar el mapeo
  requiere pulsar "Actualizar vista previa"). Si la Etapa 5 lo exige, se puede añadir HTMX
  servido desde el propio dominio sin cambiar la arquitectura.
