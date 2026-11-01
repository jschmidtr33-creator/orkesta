"""Agente de investigación: de filtros a empresas y decisores con fuentes citadas.

Flujo de una corrida:
1. Empresas: Wikidata (catálogo por país o empresas escritas por el usuario) y, si se pide,
   las empresas de la propia base que cumplen los filtros.
2. Decisores, en paralelo: directivos vigentes en Wikidata, páginas de equipo de cada sitio
   oficial y nombramientos en noticias recientes (GDELT).
3. Correos: los publicados en el sitio; con al menos uno personal se deduce el patrón de la
   empresa y se marca como «estimado» para los demás (la validación llega en la Etapa 3).

Nada se inventa: cada campo guarda de dónde salió. Lo que no aparece queda vacío.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.core.jobs import handler
from orkesta.data.models import Company
from orkesta.engine.normalize.company import normalize_company
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.engine.normalize.text import clean, fold
from orkesta.engine.taxonomy import linkedin as li
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, classify_all, size_band
from orkesta.engine.taxonomy.titles import (
    AREA_LABELS,
    DECISION_LEVELS,
    LEVEL_LABELS,
    classify_title,
)
from orkesta.research import leads, memory, news, serp, website, wikidata
from orkesta.research.extract import (
    apply_pattern,
    detect_pattern,
    email_matches_person,
    is_generic_email,
    make_person,
)
from orkesta.research.fetch import Fetcher
from orkesta.research.models import ResearchItem, ResearchRun

logger = logging.getLogger("orkesta.research")
RESEARCH_JOB = "research_run"
DEADLINE_SECONDS = 900  # las búsquedas van siempre al máximo de resultados
SITE_WORKERS = 6
LEVEL_WEIGHT = {"c_level": 50, "duenio_socio": 45, "vp": 40, "director": 35, "gerente": 25}


class Canceled(Exception):
    pass


def company_key(name: str) -> str:
    return (normalize_company(name).match_key or fold(name))[:300]


@dataclass
class Candidate:
    key: str
    name: str
    domain: str = ""
    website: str = ""
    qid: str = ""
    employees: int | None = None
    city: str = ""
    industry: str = ""
    industry_code: str = ""
    description: str = ""
    linkedin: str = ""
    from_base: bool = False
    sources: list[dict[str, Any]] = field(default_factory=list)
    emails: dict[str, str] = field(default_factory=dict)
    phones: dict[str, str] = field(default_factory=dict)
    people_found: int = 0
    site_status: str = ""
    pages_read: int = 0
    site_patterns: list[str] = field(default_factory=list)
    size_text: str = ""  # rango de empleados publicado (LinkedIn) cuando no hay cifra exacta
    mail_provider: str = ""  # google | microsoft | propio | sin_correo …
    location: str = ""

    def data(self, country: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "website": self.website or (f"https://{self.domain}" if self.domain else ""),
            "wikidata": self.qid,
            "employees": self.employees,
            "size": size_band(self.employees) or self.size_text or None,
            "location": self.location,
            "mail_provider": self.mail_provider,
            "city": self.city,
            "country": country,
            "country_name": COUNTRIES[country][0] if country in COUNTRIES else country,
            "industry": self.industry,
            "industry_code": self.industry_code,
            "description": self.description,
            "linkedin": self.linkedin,
            "from_base": self.from_base,
            "emails": sorted(self.emails),
            "phones": sorted(self.phones),
            "people_found": self.people_found,
            "site_status": self.site_status,
            "pages_read": self.pages_read,
        }


@dataclass
class Prospect:
    key: str
    company: Candidate
    full_name: str
    first_name: str
    last_name: str
    title: str = ""
    linkedin: str = ""
    email: str = ""
    email_status: str = ""  # publicado | estimado
    location: str = ""
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def tokens(self) -> set[str]:
        return {t for t in fold(self.full_name).split() if len(t) > 2}

    def set(self, name: str, value: str, source: dict[str, Any], confidence: int) -> None:
        current = self.provenance.get(name)
        if not value or (current and current["confidence"] >= confidence):
            return
        setattr(self, name, value)
        self.provenance[name] = {
            "label": source["label"],
            "url": source.get("url", ""),
            "confidence": confidence,
        }

    def add_source(self, source: dict[str, Any]) -> None:
        if all(s.get("url") != source.get("url") for s in self.sources):
            self.sources.append(source)

    def classification(self) -> tuple[str | None, str | None]:
        return classify_title(self.title)

    def score(self) -> int:
        level, _ = self.classification()
        value = LEVEL_WEIGHT.get(level or "", 10 if level else 0)
        value += 10 * min(len({s["type"] for s in self.sources}), 3)
        value += {"publicado": 25, "estimado": 10}.get(self.email_status, 0)
        value += 5 if self.linkedin else 0
        return min(value, 100)

    def data(self, country: str) -> dict[str, Any]:
        level, area = self.classification()
        return {
            "full_name": self.full_name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "title": self.title,
            "level": level,
            "level_label": LEVEL_LABELS.get(level or "", ""),
            "area": area,
            "area_label": AREA_LABELS.get(area or "", ""),
            "company": self.company.name,
            "company_key": self.company.key,
            "domain": self.company.domain,
            "email": self.email,
            "email_status": self.email_status,
            "phone": next(iter(sorted(self.company.phones)), ""),
            "linkedin": self.linkedin,
            "city": self.company.city,
            "location": self.location or self.company.city,
            "country": country,
            "industry": self.company.industry,
            "provenance": self.provenance,
        }


def _words_in(phrase: str, text: str) -> bool:
    words = fold(phrase).split()
    return bool(words) and all(w in text for w in words)


@dataclass
class Filters:
    country: str
    industries: list[str]
    city: str
    sizes: list[str]
    companies: list[str]
    levels: list[str]
    areas: list[str]
    keywords: str
    include_base: bool
    limit: int
    cities: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    exclude_sectors: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    exclude_functions: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    exclude_titles: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    first_name: str = ""
    last_name: str = ""
    mode: str = "leads"  # leads (Posible cliente) | accounts (Cuentas)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Filters:
        def strings(name: str) -> list[str]:
            return [clean(v) for v in data.get(name) or [] if clean(v)]

        city = clean(data.get("city"))
        return cls(
            country=str(data.get("country", "")),
            industries=list(data.get("industries") or []),
            city=city,
            sizes=list(data.get("sizes") or []),
            companies=strings("companies"),
            levels=list(data.get("levels") or []),
            areas=list(data.get("areas") or []),
            keywords=clean(data.get("keywords")),
            include_base=bool(data.get("include_base", True)),
            limit=int(data.get("limit") or 25),
            cities=strings("cities") or ([city] if city else []),
            sectors=list(data.get("sectors") or []),
            exclude_sectors=list(data.get("exclude_sectors") or []),
            seniority=list(data.get("seniority") or []),
            functions=list(data.get("functions") or []),
            exclude_functions=list(data.get("exclude_functions") or []),
            titles=strings("titles"),
            exclude_titles=strings("exclude_titles"),
            exclude_companies=strings("exclude_companies"),
            first_name=clean(data.get("first_name")),
            last_name=clean(data.get("last_name")),
            mode="accounts" if data.get("mode") == "accounts" else "leads",
        )

    def wants_title(self, title: str) -> bool:
        """El cargo cumple Nivel de responsabilidad, Función y Cargo actual (Sales Navigator)."""
        folded = fold(title)
        if self.seniority:
            if li.title_seniority(title) not in self.seniority:
                return False
        elif not self.titles:
            level, _ = classify_title(title)
            if level not in (self.levels or list(DECISION_LEVELS)):
                return False
        functions = li.title_functions(title)
        if self.functions and not functions & set(self.functions):
            return False
        if self.exclude_functions and functions & set(self.exclude_functions):
            return False
        if not self.functions and self.areas and not self.seniority:
            _, area = classify_title(title)
            if area not in self.areas:
                return False
        if self.titles and not any(_words_in(t, folded) for t in self.titles):
            return False
        return not any(_words_in(t, folded) for t in self.exclude_titles)

    def wants_employer(self, employer: str) -> bool:
        folded = fold(employer)
        return not any(_words_in(c, folded) for c in self.exclude_companies)

    def wants_name(self, first: str, last: str) -> bool:
        full = fold(f"{first} {last}")
        return all(not part or _words_in(part, full) for part in (self.first_name, self.last_name))

    def wants(self, prospect: Prospect) -> bool:
        return (
            self.wants_title(prospect.title)
            and self.wants_employer(prospect.company.name)
            and self.wants_name(prospect.first_name, prospect.last_name)
        )


class Run:
    """Estado compartido de una corrida y su persistencia (segura entre hilos)."""

    def __init__(self, factory: sessionmaker[Session], tenant_id: uuid.UUID, run_id: uuid.UUID):
        self.factory, self.tenant_id, self.run_id = factory, tenant_id, run_id
        self.lock = threading.RLock()
        self.log_lines: list[dict[str, str]] = []
        self.step, self.done, self.total = "empresas", 0, 0
        self.started = time.monotonic()
        self.companies: dict[str, Candidate] = {}
        self.people: dict[str, list[Prospect]] = {}
        self.filtered_out = 0
        self.source_counts = {
            "wikidata": 0, "sitio": 0, "noticia": 0, "web": 0, "perfil": 0,
        }  # fmt: skip
        self.serp = serp.SerpStats()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Una transacción con el contexto de RLS de la empresa."""
        with self.factory.begin() as session:
            set_context(session, tenant_id=self.tenant_id)
            yield session

    def expired(self) -> bool:
        return time.monotonic() - self.started > DEADLINE_SECONDS

    def log(self, text: str, *, step: str | None = None, done: int | None = None) -> None:
        with self.lock:
            if step is not None:
                self.step = step
            if done is not None:
                self.done = done
            self.log_lines.append({"at": datetime.now(UTC).isoformat(), "text": text})
            self.log_lines = self.log_lines[-40:]
            progress = {
                "step": self.step,
                "done": self.done,
                "total": self.total,
                "log": self.log_lines,
            }
            with self.session() as session:
                status = session.scalar(
                    select(ResearchRun.status).where(ResearchRun.id == self.run_id)
                )
                session.execute(
                    update(ResearchRun)
                    .where(ResearchRun.id == self.run_id)
                    .values(progress=progress)
                )
        if status == "canceled":
            raise Canceled

    def _upsert(self, kind: str, key: str, ckey: str, data: dict[str, Any], sources: list[Any],
                score: int) -> None:  # fmt: skip
        values = {
            "id": uuid.uuid4(),
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "kind": kind,
            "key": key[:400],
            "company_key": ckey,
            "data": data,
            "sources": sources,
            "score": score,
        }
        stmt = pg_insert(ResearchItem).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_research_items_run_id_kind_key",
            set_={
                "data": stmt.excluded.data,
                "sources": stmt.excluded.sources,
                "score": stmt.excluded.score,
                "updated_at": datetime.now(UTC),
            },
        )
        with self.session() as session:
            session.execute(stmt)

    def save_company(self, company: Candidate, country: str) -> None:
        with self.lock:
            score = min(100, 20 * len(company.sources) + 10 * company.people_found)
            self._upsert("company", company.key, company.key, company.data(country),
                         company.sources, score)  # fmt: skip

    def save_person(self, prospect: Prospect, country: str) -> None:
        self._upsert("person", prospect.key, prospect.company.key, prospect.data(country),
                     prospect.sources, prospect.score())  # fmt: skip

    def merge_person(
        self,
        company: Candidate,
        *,
        full_name: str,
        first_name: str,
        last_name: str,
        source: dict[str, Any],
        title: str = "",
        title_confidence: int = 70,
        linkedin: str = "",
    ) -> Prospect:
        """Une a la misma persona encontrada por varias fuentes («Juan Mora» = «Juan Mora
        Uribe» en la misma empresa) y devuelve su ficha acumulada."""
        with self.lock:
            people = self.people.setdefault(company.key, [])
            tokens = {t for t in fold(full_name).split() if len(t) > 2}
            first = fold(first_name).split()[0] if first_name.split() else ""
            match = None
            for prospect in people:
                same_first = fold(prospect.first_name).split()[:1] == [first]
                if same_first and len(tokens & prospect.tokens()) >= 2:
                    match = prospect
                    break
            if match is None:
                match = Prospect(
                    key=f"{company.key}|{fold(full_name)}",
                    company=company,
                    full_name=full_name,
                    first_name=first_name,
                    last_name=last_name,
                )
                people.append(match)
                match.provenance["full_name"] = {
                    "label": source["label"],
                    "url": source.get("url", ""),
                    "confidence": 80,
                }
            elif len(full_name) > len(match.full_name):
                # Se prefiere el nombre más completo (con segundo apellido).
                match.full_name, match.first_name, match.last_name = (
                    full_name,
                    first_name,
                    last_name,
                )
            match.add_source(source)
            match.set("title", title, source, title_confidence)
            match.set("linkedin", linkedin, source, 90)
            return match


# --- Paso 1: empresas -----------------------------------------------------------------------


def _from_wiki(item: wikidata.WikiCompany) -> Candidate:
    labels = [*item.industries, *item.types, item.description]
    codes = classify_all(labels)
    industry = item.industries[0] if item.industries else ""
    return Candidate(
        key=company_key(item.name),
        name=item.name,
        domain=item.domain,
        website=item.website,
        qid=item.qid,
        employees=item.employees,
        city=item.city,
        industry=INDUSTRY_LABELS.get(codes[0], "")
        if codes
        else industry[:1].upper() + industry[1:],
        industry_code=codes[0] if codes else "",
        description=item.description,
        linkedin=item.linkedin,
        sources=[{"type": "wikidata", "label": f"Wikidata · {item.name}", "url": item.url}],
    )


def _matches(candidate: Candidate, codes: list[str], filters: Filters) -> bool:
    if filters.industries and not set(codes) & set(filters.industries):
        return False
    if filters.city and fold(filters.city) not in fold(candidate.city):
        return False
    band = size_band(candidate.employees)
    if filters.sizes and band and band not in filters.sizes:
        return False
    if filters.keywords:
        haystack = fold(f"{candidate.name} {candidate.description} {candidate.industry}")
        if not all(word in haystack for word in fold(filters.keywords).split()):
            return False
    return True


def _looks_like_domain(value: str) -> bool:
    return "." in value and " " not in value.strip()


def discover(run: Run, fetcher: Fetcher, filters: Filters) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    if filters.companies:
        run.log(f"Buscando {len(filters.companies)} empresas indicadas en Wikidata…")
        for entry in filters.companies[:25]:
            if _looks_like_domain(entry):
                domain = entry.lower().removeprefix("https://").removeprefix("http://")
                domain = domain.removeprefix("www.").split("/")[0]
                key = company_key(domain.split(".")[0])
                found.setdefault(key, Candidate(key=key, name=domain, domain=domain))
                continue
            qids = wikidata.search_company(fetcher, entry)[:5]
            options = wikidata.companies_by_qid(fetcher, qids)
            if not options:
                key = company_key(entry)
                found.setdefault(key, Candidate(key=key, name=entry))
                if not serp.enabled():
                    run.log(f"«{entry}» no está en Wikidata. Escribe su dominio (empresa.com).")
                continue
            best = next((o for o in options if fold(entry) in fold(o.name)), options[0])
            candidate = _from_wiki(best)
            found.setdefault(candidate.key, candidate)
    else:
        country_name = COUNTRIES.get(filters.country, (filters.country,))[0]
        run.log(f"Consultando el catálogo de empresas de {country_name} en Wikidata…")
        try:
            catalog = wikidata.companies_in_country(fetcher, filters.country)
        except RuntimeError:
            catalog = []
            run.log("Wikidata no respondió a tiempo; seguimos con las demás fuentes.")
        for item in catalog:
            candidate = _from_wiki(item)
            codes = classify_all([*item.industries, *item.types, item.description])
            if _matches(candidate, codes, filters):
                found.setdefault(candidate.key, candidate)
        run.log(f"{len(found)} empresas de Wikidata cumplen los filtros.")

    if filters.include_base and not filters.companies:
        with run.session() as session:
            query = select(Company).where(
                Company.tenant_id == run.tenant_id,
                Company.domain.is_not(None),
                Company.country_code == filters.country,
            )
            if filters.industries:
                query = query.where(Company.industry_code.in_(filters.industries))
            base = session.scalars(query.limit(500)).all()
            added = 0
            for company in base:
                known = found.get(company.match_key)
                if known is not None:
                    known.from_base = True
                    continue
                candidate = Candidate(
                    key=company.match_key,
                    name=company.name,
                    domain=company.domain or "",
                    city=company.city or "",
                    industry=company.industry or "",
                    industry_code=company.industry_code or "",
                    from_base=True,
                    sources=[{"type": "base", "label": "Tu base de contactos", "url": ""}],
                )
                if _matches(candidate, [candidate.industry_code], filters):
                    found[candidate.key] = candidate
                    added += 1
        if added:
            run.log(f"{added} empresas más de tu base cumplen los filtros.")

    if serp.enabled() and not filters.companies and len(found) < 2 * filters.limit:
        country_name = COUNTRIES.get(filters.country, (filters.country,))[0]
        run.log("Buscando empresas en Google Maps…")
        places = serp.companies(
            fetcher,
            run.serp,
            country=filters.country,
            country_name=country_name,
            industries=filters.industries,
            city=filters.city,
            keywords=filters.keywords,
            limit=2 * filters.limit - len(found),
        )
        known_domains = {c.domain for c in found.values() if c.domain}
        added = 0
        code = filters.industries[0] if len(filters.industries) == 1 else ""
        for place in places:
            key = company_key(place.name)
            if place.domain in known_domains or key in found or not key:
                continue
            candidate = Candidate(
                key=key,
                name=place.name,
                domain=place.domain,
                website=place.website,
                city=place.city,
                industry=INDUSTRY_LABELS.get(code, "") or place.category,
                industry_code=code,
                sources=[
                    {
                        "type": "web",
                        "label": f"Google Maps · {place.category or 'empresa'}",
                        "url": place.website,
                    }
                ],
            )
            if place.phone:
                candidate.phones[place.phone.replace(" ", "")] = place.website
            found[key] = candidate
            added += 1
        run.log(f"Google Maps: {added} empresas con sitio web.")

    # Primero las empresas con tamaño conocido, de mayor a menor.
    ranked = sorted(
        found.values(), key=lambda c: (c.employees is None, -(c.employees or 0), c.name)
    )
    # Reserva de candidatas: si las primeras no publican decisores, se sigue con las demás.
    return ranked[: 2 * filters.limit]


# --- Paso 2: decisores ----------------------------------------------------------------------


def _wikidata_people(run: Run, fetcher: Fetcher, filters: Filters, batch: list[Candidate]) -> None:
    by_qid = {c.qid: c for c in batch if c.qid}
    if not by_qid:
        return
    try:
        people = wikidata.people_of(fetcher, list(by_qid))
    except RuntimeError:
        run.log("Wikidata no respondió la consulta de directivos.")
        return
    for person in people:
        company = by_qid.get(person.company_qid)
        if company is None:
            continue
        parsed = make_person(person.name, person.role)
        if parsed is None:
            continue
        source = {"type": "wikidata", "label": f"Wikidata · {person.name}", "url": person.url}
        prospect = run.merge_person(
            company,
            full_name=parsed.full_name,
            first_name=parsed.first_name,
            last_name=parsed.last_name,
            source=source,
            title=parsed.title,
            title_confidence=75,
            linkedin=person.linkedin,
        )
        run.source_counts["wikidata"] += 1
        _publish(run, prospect, filters)
    run.log(f"Wikidata: {len(people)} directivos vigentes en las empresas encontradas.")


def _site(
    run: Run, fetcher: Fetcher, company: Candidate, filters: Filters, add_people: bool = True
) -> None:
    """Lee el sitio oficial. Con ``add_people=False`` (modo leads) no suma personas del sitio:
    solo usa sus correos publicados para completar a los leads que ya se tienen."""
    if not company.domain or run.expired():
        return
    result = website.investigate(fetcher, company.domain, company.website)
    if add_people and not result.people and result.pages_read and serp.enabled():
        extra = [u for u in serp.team_pages(fetcher, run.serp, company.domain)]
        if extra:
            website.read_pages(fetcher, result, extra)
    with run.lock:
        company.pages_read = result.pages_read
        if result.pages_read == 0:
            company.site_status = "sin_acceso"
        else:
            company.site_status = "leido"
            company.website = company.website or result.home_url
            if company.name == company.domain and result.name:
                company.name = result.name[:200]
        for email, url in result.emails.items():
            if is_generic_email(email):
                company.emails.setdefault(email, url)
        company.phones.update({p: u for p, u in result.phones.items() if p not in company.phones})
    personal = {e: u for e, u in result.emails.items() if not is_generic_email(e)}
    if not add_people:
        with run.lock:
            for prospect in run.people.get(company.key, []):
                for email, email_url in personal.items():
                    if not prospect.email and email_matches_person(
                        email, prospect.first_name, prospect.last_name
                    ):
                        label = f"Sitio web · {_short(email_url)}"
                        prospect.set("email", email, {"label": label, "url": email_url}, 95)
                        prospect.email_status = "publicado"
                        _publish(run, prospect, filters)
            # Correos con nombre en el sitio: sirven de evidencia del formato de la empresa.
            company.site_patterns = [
                pattern
                for person, _ in result.people
                for email in personal
                if (pattern := detect_pattern(person.first_name, person.last_name, email))
            ]
        run.save_company(company, filters.country)
        return
    for person, url in result.people:
        source = {"type": "sitio", "label": f"Sitio web · {_short(url)}", "url": url}
        prospect = run.merge_person(
            company,
            full_name=person.full_name,
            first_name=person.first_name,
            last_name=person.last_name,
            source=source,
            title=person.title,
            title_confidence=90,
        )
        with run.lock:
            run.source_counts["sitio"] += 1
            for email, email_url in personal.items():
                if email_matches_person(email, prospect.first_name, prospect.last_name):
                    label = f"Sitio web · {_short(email_url)}"
                    prospect.set("email", email, {"label": label, "url": email_url}, 95)
                    prospect.email_status = "publicado"
        _publish(run, prospect, filters)
    company.people_found = len(run.people.get(company.key, []))
    run.save_company(company, filters.country)


def _news(run: Run, fetcher: Fetcher, filters: Filters, batch: list[Candidate]) -> None:
    targets = {c.key: c.name for c in batch if len(c.name) >= 4}
    if not targets:
        return
    found = news.search(fetcher, targets)
    for item in found:
        company = run.companies.get(item.company_key)
        if company is None:
            continue
        label = " · ".join(x for x in ("Noticia", item.outlet, item.date) if x)
        source = {"type": "noticia", "label": label, "url": item.url, "date": item.date,
                  "headline": item.headline}  # fmt: skip
        prospect = run.merge_person(
            company,
            full_name=item.full_name,
            first_name=item.first_name,
            last_name=item.last_name,
            source=source,
            title=item.title,
            title_confidence=60,
        )
        run.source_counts["noticia"] += 1
        _publish(run, prospect, filters)
    run.log(f"Noticias: {len(found)} menciones de directivos en los últimos 3 meses.")


def _web_people(run: Run, fetcher: Fetcher, filters: Filters, batch: list[Candidate]) -> None:
    """Buscador de Google: perfiles públicos de decisores."""
    if serp.enabled():
        _serp_people(run, fetcher, filters, batch)


def _serp_people(run: Run, fetcher: Fetcher, filters: Filters, batch: list[Candidate]) -> None:
    def one(company: Candidate) -> int:
        if run.expired() or run.serp.fatal:
            return 0
        hits = serp.decision_makers(
            fetcher, run.serp, company=company.name, domain=company.domain,
            country=filters.country, levels=filters.levels, areas=filters.areas,
        )  # fmt: skip
        for hit in hits:
            source = {"type": "perfil", "label": "Perfil público · Google", "url": hit.url}
            prospect = run.merge_person(
                company,
                full_name=hit.full_name,
                first_name=hit.first_name,
                last_name=hit.last_name,
                source=source,
                title=hit.title,
                title_confidence=65,
                linkedin=hit.url,
            )
            with run.lock:
                run.source_counts["perfil"] += 1
            _publish(run, prospect, filters)
        return len(hits)

    total = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(one, c): c for c in batch}
        for future in as_completed(futures):
            company = futures[future]
            try:
                n = future.result()
            except Exception:
                logger.exception("serp_people_failed %s", company.name)
                continue
            total += n
            if n:
                run.log(f"Buscador · {company.name}: {n} decisores con perfil público")
    if run.serp.fatal == "clave":
        run.log("Buscador: la clave SERPER_API_KEY no es válida. Revísala en Render.")
    elif run.serp.fatal == "creditos":
        run.log("Buscador: se acabaron los créditos de Serper. Recarga en serper.dev.")
    else:
        run.log(f"Buscador de Google: {total} decisores ({run.serp.calls} consultas usadas).")


def _short(url: str) -> str:
    return url.split("://", 1)[-1].removeprefix("www.").rstrip("/")[:120]


def _publish(run: Run, prospect: Prospect, filters: Filters) -> None:
    with run.lock:
        if filters.wants(prospect):
            run.save_person(prospect, filters.country)


# --- Paso 3: patrones de correo -------------------------------------------------------------


def infer_emails(run: Run, filters: Filters) -> int:
    estimated = 0
    for key, people in run.people.items():
        company = run.companies.get(key)
        if company is None or not company.domain:
            continue
        patterns = [
            detect_pattern(p.first_name, p.last_name, p.email)
            for p in people
            if p.email_status == "publicado"
        ]
        known = [p for p in patterns if p]
        if not known:
            continue
        pattern = max(set(known), key=known.count)
        source = {
            "type": "patron",
            "label": f"Patrón {pattern}@{company.domain} deducido de {len(known)} correo(s) "
            "publicados en el sitio",
            "url": "",
        }
        for prospect in people:
            if prospect.email:
                continue
            email = apply_pattern(pattern, prospect.first_name, prospect.last_name, company.domain)
            if email:
                prospect.set("email", email, source, 40)
                prospect.email_status = "estimado"
                estimated += 1
                _publish(run, prospect, filters)
    return estimated


def complete_results(
    run: Run, filters: Filters, fetcher: Fetcher, companies: list[Candidate]
) -> None:
    """Cada resultado debe traer su perfil de LinkedIn y un correo: se buscan los que falten."""
    country_name = COUNTRIES.get(filters.country, (filters.country,))[0]
    wanted = [p for c in companies for p in run.people.get(c.key, []) if filters.wants(p)]

    def profile(prospect: Prospect) -> None:
        if prospect.linkedin or run.expired() or run.serp.fatal:
            return
        found = serp.find_profile(
            fetcher, run.serp, first=prospect.first_name, last=prospect.last_name,
            company=prospect.company.name, country=filters.country, country_name=country_name,
        )  # fmt: skip
        if found is None:
            return
        source = {"type": "perfil", "label": "Perfil público · Google", "url": found.url}
        with run.lock:
            prospect.set("linkedin", found.url, source, 65)
            prospect.add_source(source)
            prospect.location = prospect.location or found.location
        _publish(run, prospect, filters)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(profile, wanted))

    missing = [c for c in companies if any(not p.email for p in run.people.get(c.key, []))]
    resolve_domains(run, fetcher, missing, filters.country)
    assign_emails(run, fetcher, filters, [c for c in missing if c.domain])


# --- Orquestación ---------------------------------------------------------------------------


def _investigate(
    run: Run, fetcher: Fetcher, filters: Filters, batch: list[Candidate], done: int
) -> int:
    """Todas las fuentes de decisores, en paralelo, para un grupo de empresas."""
    with ThreadPoolExecutor(max_workers=SITE_WORKERS + 2) as pool:
        side: list[Callable[[], None]] = [
            lambda: _wikidata_people(run, fetcher, filters, batch),
            lambda: _news(run, fetcher, filters, batch),
            lambda: _web_people(run, fetcher, filters, batch),
        ]
        side_futures = [pool.submit(fn) for fn in side]
        site_futures = {pool.submit(_site, run, fetcher, c, filters): c for c in batch}
        for future in as_completed(site_futures):
            company = site_futures[future]
            done += 1
            try:
                future.result()
            except Canceled:
                raise
            except Exception:
                logger.exception("site_failed %s", company.domain)
            found = len(run.people.get(company.key, []))
            detail = f"{found} decisores en su sitio" if found else "sitio sin equipo publicado"
            run.log(f"{company.name}: {detail}", done=done)
        for future in side_futures:
            try:
                future.result()
            except Canceled:
                raise
            except Exception:
                logger.exception("side_source_failed")
    return done


def _productive(run: Run, filters: Filters) -> int:
    return sum(1 for people in run.people.values() if any(filters.wants(p) for p in people))


COMMON_PATTERN = "first.last"


SEARCH_BUDGET = 1500  # consultas al buscador por búsqueda (cada una cuesta 1 crédito)
PEOPLE_BUDGET = 1000  # de ellas, para encontrar personas; el resto, para dominios y correos
FANOUT_COMPANIES = 80  # empresas del sector a las que se buscan decisores una por una
_LEVEL_TO_SN = {"c_level": "cxo", "duenio_socio": "propietario", "vp": "vp", "director": "director",
                "gerente": "gestion_experiencia", "jefe": "gestion_inicial"}  # fmt: skip


def resolve_domains(run: Run, fetcher: Fetcher, companies: list[Candidate], country: str) -> None:
    """Dominio de cada empresa: memoria de ORKESTA → buscador → deducido y verificado."""
    country_name = COUNTRIES.get(country, (country,))[0]

    def resolve(company: Candidate) -> None:
        if company.domain or run.expired():
            return
        domain = memory.known_domain(fetcher.factory, country, company.key)
        source = "memoria"
        if not domain and serp.enabled() and not run.serp.fatal:
            domain = serp.company_domain(fetcher, run.serp, company.name, country, country_name)
            source = "buscador"
        if not domain:
            domain = leads.guess_domain(fetcher, company.name, country)
            source = "deducido"
        if domain:
            company.domain = domain
            company.website = company.website or f"https://{domain}"
            if source != "memoria":
                memory.remember_domain(fetcher.factory, country, company.key, domain, source)
        run.save_company(company, country)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(resolve, companies))


def assign_emails(
    run: Run, fetcher: Fetcher, filters: Filters, companies: list[Candidate]
) -> dict[str, int]:
    """Correo de cada lead con empresa conocida, con la mejor evidencia disponible."""
    counts = {"learned": 0, "evidence": 0, "common": 0, "alternative": 0, "no_mx": 0}
    lock = threading.Lock()
    deep = len(companies) <= 60  # leer los sitios web toma tiempo: solo en búsquedas medianas

    def work(company: Candidate) -> None:
        missing = [p for p in run.people.get(company.key, []) if not p.email]
        if not company.domain or not missing or run.expired():
            return
        hosts = memory.mail_servers(fetcher, company.domain)
        if hosts == []:
            with lock:
                counts["no_mx"] += 1  # el dominio no recibe correo: no se inventa uno
            company.mail_provider = "sin_correo"
            return
        provider, provider_label = memory.mail_provider(hosts or [])
        company.mail_provider = provider
        pattern, confidence, label = memory.known_pattern(fetcher.factory, company.domain)
        kind = "learned"
        example = ""
        if not pattern:
            votes = 0
            if serp.enabled() and not run.serp.fatal:
                pattern, votes, example = serp.email_pattern(fetcher, run.serp, company.domain)
            if not pattern and deep:
                _site(run, fetcher, company, filters, add_people=False)  # correos del sitio
                if company.site_patterns:
                    pattern = max(set(company.site_patterns), key=company.site_patterns.count)
                    votes, example = company.site_patterns.count(pattern), company.website
            if pattern:
                memory.remember_pattern(fetcher.factory, company.domain, pattern, votes, example)
                kind, confidence = "evidence", 50 if votes > 1 else 40
                shown = memory.PATTERN_LABELS.get(pattern, pattern)
                label = f"Formato {shown}@{company.domain} visto en {votes} correo(s) públicos"
        if not pattern:
            pattern, kind, confidence = COMMON_PATTERN, "common", 30 if provider else 20
            label = f"Formato más común (nombre.apellido@{company.domain}); por validar"
            if provider_label:
                label += f" · el dominio recibe correo ({provider_label})"
        source = {"type": "patron", "url": example, "label": label}
        # Lo aprendido de las campañas: formatos descartados por rebotes y direcciones que ya
        # rebotaron nunca se vuelven a proponer; se prueba el siguiente formato más probable.
        chain, bounced = memory.candidates(fetcher.factory, company.domain, pattern)
        for prospect in missing:
            chosen = ""
            for option in chain:
                email = apply_pattern(
                    option, prospect.first_name, prospect.last_name, company.domain
                )
                if email and memory.address_hash(email) not in bounced:
                    chosen = option
                    break
            if not chosen or not email:
                continue
            if chosen == pattern:
                use_source, use_conf, use_kind = source, confidence, kind
            else:
                shown = memory.PATTERN_LABELS.get(chosen, chosen)
                alt = f"Formato alternativo ({shown}@{company.domain}): el anterior rebotó"
                use_source = {"type": "patron", "url": "", "label": alt}
                use_conf, use_kind = 25, "alternative"
            with run.lock:
                prospect.set("email", email, use_source, use_conf)
                prospect.email_status = "estimado"
            with lock:
                counts[use_kind] += 1
            _publish(run, prospect, filters)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, companies))
    return counts


def execute_leads_first(run: Run, filters: Filters, fetcher: Fetcher) -> dict[str, int]:
    """Motor v2: cargos reales → abanico de consultas → cuentas del sector → dominio → correo."""
    country_name = COUNTRIES.get(filters.country, (filters.country,))[0]
    run.serp.budget = SEARCH_BUDGET
    run.total = 0
    run.log(
        f"Buscando todos los posibles clientes que cumplen los filtros en {country_name}…",
        step="leads",
    )
    seniority = filters.seniority or [
        _LEVEL_TO_SN[lv] for lv in filters.levels if lv in _LEVEL_TO_SN
    ]
    phrases = leads.title_phrases(seniority, filters.functions, filters.titles)
    sector_terms = li.sector_terms(filters.sectors, 6) or list(dict.fromkeys(
        t for c in filters.industries for t in serp.INDUSTRY_TERMS.get(c, [])))[:6]  # fmt: skip
    excluded = set(li.sector_parents(filters.exclude_sectors)) - set(filters.industries)
    code = filters.industries[0] if len(filters.industries) == 1 else ""
    found = 0

    def on_hit(hit: leads.Hit) -> None:
        nonlocal found
        key = company_key(hit.employer)
        if not key:
            return
        with run.lock:
            company = run.companies.get(key)
            if company is None:
                company = Candidate(
                    key=key, name=hit.employer, domain=hit.domain,
                    website=f"https://{hit.domain}" if hit.domain else "",
                    city=filters.city, industry=INDUSTRY_LABELS.get(code, ""), industry_code=code,
                    sources=[{"type": "perfil", "label": "Empresa del perfil", "url": hit.url}],
                )  # fmt: skip
                run.companies[key] = company
            elif hit.domain and not company.domain:
                company.domain = hit.domain
        prospect = run.merge_person(
            company, full_name=hit.full_name, first_name=hit.first_name, last_name=hit.last_name,
            title=hit.title, title_confidence=65, linkedin=hit.url,
            source={"type": "perfil", "label": "Perfil público · Google", "url": hit.url},
        )  # fmt: skip
        with run.lock:
            run.source_counts["perfil"] += 1
            prospect.location = prospect.location or hit.location
            found += 1
            done = found
        _publish(run, prospect, filters)
        if done % 10 == 0:
            run.log(f"{done} posibles clientes encontrados…", done=done)

    def accept(title: str, employer: str, first: str, last: str) -> bool:
        return (
            filters.wants_title(title)
            and filters.wants_employer(employer)
            and filters.wants_name(first, last)
        )

    collector = leads.Collector(
        fetcher, run.serp, country=filters.country, country_name=country_name,
        cities=filters.cities, accept=accept, industries=filters.industries, excluded=excluded,
        target=filters.limit, on_hit=on_hit, expired=run.expired, max_calls=PEOPLE_BUDGET,
    )  # fmt: skip
    name = f"{filters.first_name} {filters.last_name}".strip()
    exclude = [*filters.exclude_titles, *filters.exclude_companies]
    exclude += [li.SECTORS[c][2][0] for c in filters.exclude_sectors if c in li.SECTORS]
    plan = leads.plan_people(
        country=filters.country, country_name=country_name, cities=filters.cities,
        phrases=phrases, sector_terms=sector_terms, keywords=filters.keywords,
        companies=filters.companies, exclude=exclude, first_last=name,
    )  # fmt: skip
    collector.run_all(plan)

    # Cuentas → personas: empresas del sector y sus decisores, una por una.
    if (filters.sectors or filters.industries or filters.keywords) and not collector.full():
        run.log("Ampliando: buscando empresas del sector y sus decisores…", done=found)
        accounts: dict[str, tuple[str, str]] = {}
        for hit in serp.linkedin_companies(
            fetcher, run.serp, country=filters.country, country_name=country_name,
            cities=filters.cities, sector_terms=sector_terms, exclude_terms=exclude,
            keywords=filters.keywords, max_pages=5,
        ):  # fmt: skip
            accounts.setdefault(company_key(hit.name), (hit.name, ""))
        for place in serp.companies(
            fetcher, run.serp, country=filters.country, country_name=country_name,
            industries=filters.industries, city=filters.city, keywords=filters.keywords, limit=60,
        ):  # fmt: skip
            key = company_key(place.name)
            accounts[key] = (place.name, place.domain)
            memory.remember_domain(fetcher.factory, filters.country, key, place.domain, "maps")
        roles = " OR ".join(f'"{t}"' for t in (phrases[:6] or ["Gerente", "Director"]))
        tasks = [(f'site:linkedin.com/in "{nm}" ({roles})', nm, dom)
                 for nm, dom in list(accounts.values())[:FANOUT_COMPANIES]]  # fmt: skip

        def fan(task: tuple[str, str, str]) -> None:
            if not collector.full():
                collector.run_query(task[0], max_pages=2, company=task[1], domain=task[2])

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fan, tasks))
    run.log(f"{found} posibles clientes encontrados.", done=found)
    companies = list(run.companies.values())
    if not companies:
        run.log("No aparecieron posibles clientes con estos filtros. Prueba con menos filtros.")
        return {"companies": 0, "people": 0, **run.serp.totals()}

    run.log(f"Identificando el sitio web de {len(companies)} empresas…", step="dominios")
    resolve_domains(run, fetcher, companies, filters.country)
    with_domain = [c for c in companies if c.domain]
    run.log(f"{len(with_domain)} empresas con sitio web identificado.", step="correos")
    counts = assign_emails(run, fetcher, filters, with_domain)
    shown = [p for people in run.people.values() for p in people if filters.wants(p)]
    for company in companies:
        company.people_found = sum(1 for p in shown if p.company.key == company.key)
        run.save_company(company, filters.country)
    return {
        "companies": len(companies),
        "people": len(shown),
        "with_email": sum(1 for p in shown if p.email_status == "publicado"),
        "estimated_emails": sum(1 for p in shown if p.email_status == "estimado"),
        "learned_emails": counts["learned"],
        "pattern_emails": counts["evidence"],
        "common_emails": counts["common"],
        "domains_without_mail": counts["no_mx"],
        "alternative_emails": counts["alternative"],
        "domains": len(with_domain),
        "filtered_out": sum(len(v) for v in run.people.values()) - len(shown),
        **{f"source_{k}": v for k, v in run.source_counts.items()},
        **run.serp.totals(),
    }


def execute_accounts(run: Run, filters: Filters, fetcher: Fetcher) -> dict[str, int]:
    """Búsqueda de Cuentas (Sales Navigator): empresas que cumplen los filtros de empresa."""
    country_name = COUNTRIES.get(filters.country, (filters.country,))[0]
    run.log("Buscando cuentas que cumplen tus filtros…", step="cuentas")
    found: dict[str, Candidate] = {}
    excluded = set(li.sector_parents(filters.exclude_sectors)) - set(filters.industries)
    if serp.enabled():
        hits = serp.linkedin_companies(
            fetcher, run.serp, country=filters.country, country_name=country_name,
            cities=filters.cities, sector_terms=li.sector_terms(filters.sectors, 6),
            exclude_terms=[li.SECTORS[c][2][0] for c in filters.exclude_sectors if c in li.SECTORS],
            keywords=filters.keywords,
        )  # fmt: skip
        label = li.SECTORS[filters.sectors[0]][0] if len(filters.sectors) == 1 else ""
        for hit in hits:
            codes = set(classify_all([hit.industry, hit.name, hit.about]))
            if filters.industries and codes and not codes & set(filters.industries):
                continue
            if excluded and codes and codes <= excluded:
                continue
            if filters.sizes and hit.size and hit.size not in filters.sizes:
                continue
            key = company_key(hit.name)
            if not key or key in found:
                continue
            fallback = INDUSTRY_LABELS.get(min(codes), "") if codes else ""
            source = {"type": "perfil", "label": "Página de LinkedIn", "url": hit.linkedin}
            found[key] = Candidate(
                key=key, name=hit.name, linkedin=hit.linkedin,
                industry=hit.industry or label or fallback,
                industry_code=min(codes) if codes else "", description=hit.about,
                size_text=hit.size, location=hit.location,
                city=hit.location.split(",")[0] if "," in hit.location else "",
                sources=[source],
            )  # fmt: skip
            run.save_company(found[key], filters.country)
        run.log(f"{len(found)} cuentas encontradas.", done=len(found))
    for candidate in discover(run, fetcher, filters):
        if candidate.key not in found:
            found[candidate.key] = candidate
            run.save_company(candidate, filters.country)
    companies = list(found.values())
    run.log(f"Identificando el sitio web de {len(companies)} cuentas…", step="dominios")

    def resolve(company: Candidate) -> None:
        if company.domain or run.expired() or run.serp.fatal or not serp.enabled():
            return
        company.domain = serp.company_domain(
            fetcher, run.serp, company.name, filters.country, country_name
        )
        if company.domain:
            company.website = company.website or f"https://{company.domain}"
            run.save_company(company, filters.country)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(resolve, companies))
    return {
        "companies": len(companies),
        "people": 0,
        "with_domain": sum(1 for c in companies if c.domain),
        **run.serp.totals(),
    }


def execute(run: Run, filters: Filters, fetcher: Fetcher) -> dict[str, int]:
    if filters.mode == "accounts":
        return execute_accounts(run, filters, fetcher)
    if serp.enabled() and not filters.companies:
        return execute_leads_first(run, filters, fetcher)
    pool = discover(run, fetcher, filters)
    first, reserve = pool[: filters.limit], pool[filters.limit :]
    companies = list(first)
    run.companies = {c.key: c for c in companies}
    for company in companies:
        run.save_company(company, filters.country)
    run.total = len(companies)
    if not companies:
        run.log("Ninguna empresa cumple los filtros. Prueba con menos filtros o escribe empresas.")
        return {"companies": 0, "people": 0}
    run.log(
        f"Investigando {len(companies)} empresas: directivos, sitios oficiales, noticias"
        + (" y buscador de Google…" if serp.enabled() else "…"),
        step="decisores",
        done=0,
    )
    done = _investigate(run, fetcher, filters, companies, 0)
    # Segunda ola: si varias empresas no publican a sus decisores, se prueban otras que
    # también cumplen los filtros, hasta completar el objetivo o agotar el tiempo.
    missing = filters.limit - _productive(run, filters)
    if missing > 0 and reserve and not run.expired() and not run.serp.fatal:
        extra = reserve[: max(missing, 3)]
        run.log(f"Ampliando la búsqueda a {len(extra)} empresas más para completar decisores…")
        for company in extra:
            run.companies[company.key] = company
            run.save_company(company, filters.country)
        companies += extra
        run.total = len(companies)
        done = _investigate(run, fetcher, filters, extra, done)
    run.log("Deduciendo patrones de correo…", step="correos")
    estimated = infer_emails(run, filters)
    if serp.enabled():
        complete_results(run, filters, fetcher, companies)
    shown = [p for people in run.people.values() for p in people if filters.wants(p)]
    for company in companies:
        company.people_found = sum(1 for p in shown if p.company.key == company.key)
        run.save_company(company, filters.country)
    return {
        "companies": len(companies),
        "people": len(shown),
        "with_email": sum(1 for p in shown if p.email_status == "publicado"),
        "estimated_emails": sum(1 for p in shown if p.email_status == "estimado"),
        "inferred": estimated,
        "filtered_out": sum(len(v) for v in run.people.values()) - len(shown),
        **{f"source_{k}": v for k, v in run.source_counts.items()},
        **run.serp.totals(),
    }


def _finish(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, run_id: uuid.UUID, **values: Any
) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run_id, ResearchRun.status != "canceled")
            .values(finished_at=datetime.now(UTC), **values)
        )


def _on_failure(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any], detail: str
) -> None:
    _finish(factory, tenant_id, uuid.UUID(payload["run_id"]), status="failed",
            error="La investigación falló. Intenta de nuevo en unos minutos.")  # fmt: skip


@handler(RESEARCH_JOB, on_failure=_on_failure)
def run_research(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    run_id = uuid.UUID(payload["run_id"])
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        record = session.get(ResearchRun, run_id)
        if record is None or record.status == "canceled":
            return
        record.status = "running"
        filters = Filters.from_payload(record.filters)
    run = Run(factory, tenant_id, run_id)
    try:
        with Fetcher(factory) as fetcher:
            totals = execute(run, filters, fetcher)
            totals["requests"] = fetcher.requests
        people, companies = totals.get("people", 0), totals.get("companies", 0)
        run.log(f"Listo: {companies} empresas y {people} decisores.", step="listo", done=run.total)
    except Canceled:
        return
    _finish(factory, tenant_id, run_id, status="completed", totals=totals)
