# ADR 0001: Motor de machine learning propio, sin LLM ni buscador obligatorio

- Estado: aceptada
- Fecha: 2026-10-28

## Contexto

La versión anterior de ORKESTA dependía de Gemini y Google Search en Apps Script. Eso ataba el
núcleo del producto a un proveedor, con coste por uso y límites de ejecución.

## Decisión

El núcleo son dos modelos supervisados propios (LightGBM): uno elige el dominio oficial entre
candidatos y otro predice el patrón de correo. Se entrenan con datos de ORKESTA: pares
empresa→sitio de Wikidata y OpenStreetMap, correos observados en sitios corporativos, el conjunto
etiquetado y las confirmaciones del usuario. Mientras no haya modelo entrenado decide un baseline
de reglas. Un modelo nuevo solo se activa si supera al baseline y al modelo anterior.

Las fuentes de evidencia son adaptadores intercambiables. Un buscador de pago puede añadirse
como adaptador opcional, nunca como dependencia del núcleo.

## Consecuencias

- Coste variable de IA nulo; inferencia en CPU dentro del worker.
- La cobertura depende de las fuentes disponibles: ningún modelo crea datos. Los nombres de
  empresa genéricos son el principal riesgo y se medirán en la Etapa 4.
- No se verifica la existencia de buzones por SMTP.
