# ADR 0005: Resolución de dominios por reglas explicables y evaluación en vivo en GitHub Actions

- Estado: aceptada
- Fecha: 2026-10-29

## Contexto

El MVP recibe solo Nombre, Cargo, Empresa y Ubicación. El dominio debe descubrirse sin
buscadores ni LLM. El entorno donde se desarrolla ORKESTA no tiene salida a internet, y Railway
no admite cuentas nuevas desde su incidente de octubre de 2026.

## Decisión

- **Unificación conservadora de empresas:** se ignoran calificativos de país, región, sector y
  forma jurídica, pero no "grupo". Unir dos empresas distintas es peor que dejar separadas dos
  variantes.
- **Fuentes:** candidatos generados por nombre y país, dominios escritos en el nombre y sitio
  oficial de Wikidata (P856). OpenStreetMap queda fuera: la política de uso de Nominatim no
  permite consultas masivas.
- **Verificación:** DNS, MX y visita de la portada con protección SSRF, redirecciones
  verificadas y robots.txt según RFC 9309. Un dominio que redirige a otro cede su lugar al
  destino; uno aparcado o que lleva a una red social se descarta.
- **Puntuación `rules-v1`:** pesos versionados en `engine/domain/scoring.py`. "Confirmado" exige
  ≥ 70 puntos y evidencia de identidad (nombre o marca en el título, Wikidata o dominio escrito
  en el nombre); que un dominio exista y se parezca al nombre nunca basta.
- **Evaluación:** el motor funciona sin base de datos, así que se mide en vivo desde un flujo
  manual de GitHub Actions contra `data/eval/empresas_mx.csv`. Ese conjunto solo contiene
  empresas: los datos de personas nunca entran en el repositorio.

## Consecuencias

- Cada resultado lleva razones legibles, que sirven a la vez para revisarlo y como variables
  del modelo de la Etapa 3C.
- La exactitud real solo se conoce tras la evaluación en vivo. Las empresas sin etiqueta se
  revisan a mano con el informe, y esas revisiones son las etiquetas del entrenamiento.
- Riesgo residual: DNS rebinding entre la verificación y la conexión (ver `engine/net/fetch.py`).
