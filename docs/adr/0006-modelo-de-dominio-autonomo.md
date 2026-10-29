# ADR 0006 — Modelo de dominio propio y bucle autónomo de mejora

Fecha: 2026-10-29 · Estado: aceptado

## Contexto

Las reglas `rules-v1` deciden el dominio corporativo con pesos fijados a mano. Se pidió que el
motor mejore solo, sin que una persona etiquete o revise resultados.

## Decisión

1. **Etiquetas automáticas.** Wikidata (sitio oficial P856) da ~1.200 empresas LATAM de 11
   países. Para no copiar la respuesta, la evaluación no consulta Wikidata como fuente.
2. **Bucle en GitHub Actions** (`auto-optimizacion.yml`): cada push a `auto/**` y cada lunes
   mide con internet real, vuelca variables por candidato, entrena un modelo candidato y
   publica todo en la rama `informes`.
3. **Modelo:** LightGBM binario por candidato. Partición por empresa; umbrales por validación
   cruzada (5 grupos); "probable" = menor umbral con 75 % de exactitud.
4. **Promoción solo si gana** en empresas nunca vistas: mejor exactitud estricta con cobertura
   similar, o igual exactitud con más cobertura, sin empeorar la exactitud de marca.
   Con menos de 30 empresas de prueba nunca se promueve.
5. **Producción sin dependencias nativas:** los árboles se exportan a JSON y se recorren en
   Python puro (`engine/domain/model.py`), con predicción idéntica a LightGBM.
6. **Confirmado** = reglas con evidencia de identidad y modelo sobre su umbral de probable.
   Las reglas siguen como respaldo si no hay modelo promovido.

## Resultado de la primera promoción (`lgbm-20261029`)

342 empresas de prueba nunca vistas: exactitud estricta 74,0 % (reglas 63,0 %), exactitud de
marca 82,8 % (reglas 70,1 %), cobertura 76,3 % (reglas 74,3 %).

## Consecuencias

- Las etiquetas de Wikidata tienen ruido (una sola extensión por empresa, marcas distintas
  del nombre legal); por eso se informa también la exactitud de marca.
- La extensión exacta que recibe correo (alpina.com frente a alpina.com.co) la resuelve la fase
  de patrones de correo con registros MX.
