# ADR 0002: Cola de trabajos en PostgreSQL, sin Redis

- Estado: aceptada
- Fecha: 2026-10-28

## Contexto

El enriquecimiento tarda segundos por empresa y los lotes pueden durar minutos u horas. Hace
falta procesamiento en segundo plano con reintentos, reanudación y cancelación.

## Decisión

La cola es la tabla `enrichment_jobs`. Los workers reclaman con `SELECT ... FOR UPDATE SKIP
LOCKED`, que permite varios workers concurrentes sin que dos tomen el mismo trabajo.

- Reintentos con espera exponencial (`JOB_RETRY_BASE_SECONDS` hasta `JOB_RETRY_MAX_SECONDS`).
- Un trabajo `running` cuyo bloqueo supera `JOB_LOCK_TIMEOUT_SECONDS` vuelve a `pending`; el
  intento interrumpido cuenta para el máximo.
- `(batch_id, idempotency_key)` es único: reencolar no duplica trabajo.
- Cada transición queda en `job_events`.

## Consecuencias

- Un servicio menos que pagar, operar y respaldar; el estado de la cola está en los mismos
  backups que los datos.
- Adecuado para miles de trabajos por lote. Si el volumen llega a decenas de miles de trabajos
  por minuto, se reevaluará una cola dedicada.
