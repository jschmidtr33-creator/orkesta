"""Manejador del worker para el motor de envío de campañas."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from orkesta.campaigns.service import run_tick
from orkesta.campaigns.transports import TransportFactory, default_transport
from orkesta.db.models import EnrichmentJob
from orkesta.worker.runner import PermanentJobError

# Se puede sustituir en pruebas por un transporte simulado.
transport_factory: TransportFactory = default_transport


def handle_campaign_tick(session: Session, job: EnrichmentJob) -> None:
    try:
        campaign_id = uuid.UUID(str(job.payload["campaign_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("Trabajo de campaña sin identificador válido") from exc
    run_tick(session, campaign_id, transport_factory)
