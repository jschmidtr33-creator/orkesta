// Infraestructura de ORKESTA en Railway (Infrastructure as Code).
//
// Tres recursos: servicio web (API), servicio worker (cola de enriquecimiento) y PostgreSQL.
// Web y worker se construyen desde el mismo repositorio y el mismo Dockerfile; solo cambia el
// comando de inicio. El repositorio de GitHub se conecta a cada servicio desde el panel de
// Railway (ver README), por eso aquí no se declara `source`.
//
// Comandos: `railway config plan` para revisar cambios y `railway config apply` para aplicarlos.
import { defineRailway, postgres, preserve, project, service } from "railway/iac";

export default defineRailway((ctx) => {
  const environment = ctx.environment === "production" ? "production" : "staging";
  const db = postgres("postgres");

  const shared = {
    ENVIRONMENT: environment,
    LOG_LEVEL: "INFO",
    DATABASE_URL: db.env.DATABASE_URL,
    // Se define a mano en Railway (Variables) y nunca se escribe en el repositorio.
    SECRET_KEY: preserve(),
  };

  const web = service("orkesta-web", {
    start:
      "uvicorn --factory orkesta.api.main:create_app --host 0.0.0.0 --port ${PORT:-8000} " +
      "--proxy-headers --forwarded-allow-ips='*'",
    // Las migraciones corren antes de cada despliegue; si fallan, el despliegue se detiene.
    preDeploy:
      "alembic upgrade head && python -m orkesta.cli bootstrap-admin && python -m orkesta.cli catalog-load",
    healthcheck: "/health",
    healthcheckTimeout: 60,
    env: {
      ...shared,
      // Primer administrador: se crea en el primer despliegue y luego se ignora.
      // Solo la web las recibe; el worker no las necesita.
      ORKESTA_BOOTSTRAP_ADMIN_EMAIL: preserve(),
      ORKESTA_BOOTSTRAP_ADMIN_PASSWORD: preserve(),
      ORKESTA_BOOTSTRAP_ORG_NAME: "America Digital",
    },
  });

  const worker = service("orkesta-worker", {
    start: "python -m orkesta.worker",
    env: {
      ...shared,
      WORKER_POLL_INTERVAL_SECONDS: "2",
      JOB_LOCK_TIMEOUT_SECONDS: "600",
    },
  });

  return project("orkesta", { resources: [db, web, worker] });
});
