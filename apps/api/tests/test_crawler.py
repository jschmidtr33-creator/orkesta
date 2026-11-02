"""Rastreador propio de sitios: correos ocultos (Cloudflare, «[arroba]»), datos que la empresa
publica de sí misma y formato de correo aprendido del sitio. Sitio ficticio."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import crawler, fetch, memory
from orkesta.research.models import CompanyDirectory


def _cf(email: str, key: int = 0x3C) -> str:
    return format(key, "02x") + "".join(format(ord(c) ^ key, "02x") for c in email)


HOME = """<html><head><title>Andes Logística</title>
<meta name="description" content="Operador logístico en Colombia.">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Organization",
 "name": "Andes Logística", "telephone": "+57 601 555 1234", "numberOfEmployees": 320,
 "sameAs": ["https://www.linkedin.com/company/andes-logistica"]}</script></head>
<body><nav><a href="/nosotros/equipo">Nuestro equipo</a></nav>
<p>Escríbenos: info [arroba] andeslogistica [punto] co</p></body></html>"""
TEAM = f"""<html><body><h1>Equipo directivo</h1>
<div><h3>Laura Gómez</h3><p>Gerente General</p>
<a href="/cdn-cgi/l/email-protection#{_cf("lgomez@andeslogistica.co")}">[email protected]</a></div>
<div><h3>Pedro Ruiz</h3><p>Director Financiero</p><p>pruiz [at] andeslogistica [dot] co</p></div>
</body></html>"""


def _site(request: httpx.Request) -> httpx.Response:
    if request.url.host.endswith("andeslogistica.co"):
        body = {"/": HOME, "/nosotros/equipo": TEAM}.get(request.url.path)
        if body:
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    return httpx.Response(404, text="no")


@pytest.fixture(autouse=True)
def fake_site() -> Iterator[None]:
    fetch.TRANSPORT = httpx.MockTransport(_site)
    fetch.RESOLVER = lambda host: ["93.184.216.34"]
    yield
    fetch.TRANSPORT, fetch.RESOLVER = None, None


def test_crawler_reads_company_site(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(pg_insert(CompanyDirectory).values(
            key="CO|900111222", country="CO", name="ANDES LOGISTICA S.A.S.",
            name_key="andes logistica", domain="andeslogistica.co", search_text="andes",
        ))  # fmt: skip
    assert crawler.crawl_batch(session_factory) == 1
    with session_factory() as session:
        row = session.get(CompanyDirectory, "CO|900111222")
        assert row is not None and row.site_status == "leido"
        assert row.phone == "+576015551234" and row.company_email == "info@andeslogistica.co"
        assert row.linkedin == "https://www.linkedin.com/company/andes-logistica"
        assert row.employees == 320 and row.description == "Operador logístico en Colombia."
        assert row.email_pattern == "flast"
        overview = crawler.overview(session)
        assert overview["by_status"]["leido"] == 1 and overview["patterns"] == 1
    # El formato quedó en la memoria: la próxima búsqueda lo usa sin leer el sitio.
    assert memory.known_pattern(session_factory, "andeslogistica.co")[0] == "flast"
    # Ya leído: no se vuelve a rastrear hasta dentro de 60 días.
    assert crawler.crawl_batch(session_factory) == 0
