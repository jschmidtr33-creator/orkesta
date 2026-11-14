"""Agregar contactos pegando direcciones de perfiles de LinkedIn.

ORKESTA nunca abre LinkedIn: solo usa la dirección. Si la persona ya está en el índice propio
(bases autorizadas, sitios oficiales y búsquedas anteriores) trae sus datos al instante; si no,
toma el nombre de la dirección («/in/carlos-ramirez» → Carlos Ramírez) y la empresa que escriba el
usuario. Después, el motor de enriquecimiento busca el sitio de la empresa y arma y verifica el
correo.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

MAX_URLS = 200
_URL = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)", re.IGNORECASE)
# Sufijos que LinkedIn agrega para no repetir direcciones: «carlos-ramirez-4b2a91».
_NOISE = re.compile(r"^(?=.*\d)[a-z0-9]{4,}$")
_LOWER = {"de", "del", "la", "las", "los", "y", "da", "do", "dos", "van", "von"}


@dataclass
class Profile:
    url: str
    slug: str
    first: str
    last: str


def parse(text: str) -> tuple[list[Profile], list[str]]:
    """Perfiles válidos (sin repetir) y líneas que no son perfiles de LinkedIn."""
    from urllib.parse import unquote

    found: dict[str, Profile] = {}
    invalid: list[str] = []
    for line in re.split(r"[\s,;]+", text or ""):
        line = line.strip(" <>\"'")
        if not line:
            continue
        m = _URL.search(line)
        if not m:
            invalid.append(line[:120])
            continue
        slug = unquote(m.group(1)).strip("/").lower()
        words = [w for w in re.split(r"[-_.]+", slug) if w]
        while len(words) > 1 and _NOISE.match(words[-1]):
            words.pop()
        names = [w if w in _LOWER else w[:1].upper() + w[1:] for w in words if not w.isdigit()]
        if not names:
            invalid.append(line[:120])
            continue
        first, last = names[0], " ".join(names[1:])
        found.setdefault(slug, Profile(f"https://www.linkedin.com/in/{slug}", slug, first, last))
    return list(found.values())[:MAX_URLS], invalid


def _from_index(session: Session, profiles: list[Profile], company: str,
                country: str) -> dict[str, Any]:  # fmt: skip
    """Perfiles que ORKESTA ya conoce: por su dirección o por nombre + empresa."""
    from orkesta.research.directory import name_key
    from orkesta.research.models import PersonIndex
    from orkesta.research.people import person_key

    by_url = {f"li:/in/{p.slug}": p.slug for p in profiles}
    out: dict[str, Any] = {}
    for row in session.scalars(select(PersonIndex).where(PersonIndex.key.in_(list(by_url)))):
        out[by_url[row.key]] = row
    for row in session.scalars(
        select(PersonIndex).where(PersonIndex.linkedin.is_not(None),
                                  PersonIndex.linkedin.in_([p.url for p in profiles]))
    ):  # fmt: skip
        slug = (row.linkedin or "").rstrip("/").rsplit("/in/", 1)[-1].lower()
        out.setdefault(slug, row)
    if company and country:
        ckey = name_key(company)
        keys = {person_key(country, ckey, f"{p.first} {p.last}"): p.slug for p in profiles
                if p.slug not in out}  # fmt: skip
        for row in session.scalars(select(PersonIndex).where(PersonIndex.key.in_(list(keys)))):
            out.setdefault(keys[row.key], row)
    return out


def _split_glued(session: Session, profiles: list[Profile]) -> None:
    """«/in/santiagojoyaibarra» viene sin separadores: se corta por el nombre de pila más largo
    que ORKESTA conoce del índice de personas («Santiago» + «Joyaibarra»)."""
    from sqlalchemy import func

    from orkesta.engine.normalize.text import fold
    from orkesta.research.models import PersonIndex

    glued = [p for p in profiles if not p.last and len(p.first) >= 6]
    if not glued:
        return
    names = {
        fold(n) for (n,) in session.execute(
            select(func.lower(PersonIndex.first_name)).where(PersonIndex.first_name.is_not(None))
            .group_by(func.lower(PersonIndex.first_name)).having(func.count() >= 2).limit(20000)
        ) if n and n.isalpha() and len(n) >= 3
    }  # fmt: skip
    for p in glued:
        word = fold(p.first)
        for cut in range(len(word) - 3, 2, -1):
            if word[:cut] in names:
                p.first, p.last = word[:cut].capitalize(), word[cut:].capitalize()
                break


def _search_on() -> bool:
    from orkesta.research import serp

    return serp.enabled()


def _from_search(session: Session, profiles: list[Profile]) -> dict[str, Any]:
    """Cargo, empresa y ubicación del resultado público de Google para cada dirección (en
    paralelo). Vacío si el buscador no está configurado."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import sessionmaker

    from orkesta.research import serp
    from orkesta.research.fetch import Fetcher

    if not profiles or not _search_on():
        return {}
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    stats = serp.SerpStats(budget=len(profiles) * 4 + 5)
    out: dict[str, Any] = {}
    with Fetcher(factory) as fetcher, ThreadPoolExecutor(max_workers=8) as pool:
        hits = pool.map(lambda p: (p.slug, serp.profile_by_url(fetcher, stats, p.slug)), profiles)
        for slug, hit in hits:
            if hit is not None:
                out[slug] = hit
    if stats.fatal or (stats.errors and not out):
        # El buscador respondió con error (sin créditos, clave inválida): se avisa en pantalla.
        out["__error__"] = stats.fatal or ", ".join(stats.errors)
    return out


def _refresh(session: Session, tenant_id: uuid.UUID, url: str, data: Any) -> uuid.UUID | None:
    """Un contacto que ya se agregó por su dirección y quedó incompleto se completa ahora."""
    from orkesta.data.models import Contact
    from orkesta.data.service import _clean_input, search_text
    from orkesta.engine.taxonomy.titles import classify_title

    contact = session.scalar(select(Contact).where(Contact.tenant_id == tenant_id,
                                                   Contact.linkedin_url == url))  # fmt: skip
    if contact is None:
        return None
    values = _clean_input(data)
    changed = False
    if not contact.last_name and values["last_name"]:
        contact.first_name, contact.last_name = values["first_name"], values["last_name"]
        contact.full_name = values["full_name"]
        changed = True
    for field in ("job_title", "company_name", "city", "country_code"):
        if values.get(field) and not getattr(contact, field):
            setattr(contact, field, values[field])
            changed = True
    if changed:
        contact.level_code, contact.area_code = classify_title(contact.job_title)
        contact.search_text = search_text(contact.full_name, contact.email, contact.company_name,
                                          contact.job_title, contact.city)  # fmt: skip
    return contact.id if changed else None


def _seen_profile(seen: dict[str, str] | None) -> Any:
    """El perfil tal como la persona lo ve en pantalla (lo envía la extensión)."""
    from orkesta.engine.normalize.location import parse_location
    from orkesta.research.serp import UrlProfile

    if not seen or not (seen.get("name") or seen.get("title")):
        return None
    loc = parse_location(seen.get("location") or "")
    return UrlProfile(name=" ".join((seen.get("name") or "").split())[:200],
                      role=" ".join((seen.get("title") or "").split())[:300],
                      employer=" ".join((seen.get("company") or "").split())[:200],
                      city=loc.city or "", country=loc.country_code or "")  # fmt: skip


def add(session: Session, *, tenant_id: uuid.UUID, user: Any, text: str, company: str = "",
        country: str = "", list_id: uuid.UUID | None = None,
        seen: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Crea los contactos y devuelve el resumen (los nuevos sin correo quedan para enriquecer)."""
    from orkesta.data.models import Contact
    from orkesta.data.service import ContactInput, DataError, add_to_list, create_contact
    from orkesta.engine.normalize.location import COUNTRIES

    profiles, invalid = parse(text)
    if not profiles:
        raise DataError("Pega al menos una dirección de perfil de LinkedIn (linkedin.com/in/…).")
    company = " ".join(company.split())[:200]
    code = country.strip().upper()
    code = code if code in COUNTRIES else ""
    _split_glued(session, profiles)
    known = _from_index(session, profiles, company, code)
    on_screen = _seen_profile(seen) if len(profiles) == 1 else None
    if on_screen is not None:
        # Lo que se ve en pantalla manda; el índice solo aporta lo que falte (por ejemplo, el
        # correo publicado).
        found = {profiles[0].slug: on_screen}
    else:
        found = _from_search(session, [p for p in profiles if p.slug not in known])
    search_error = str(found.pop("__error__", ""))
    created: list[uuid.UUID] = []
    existing = 0
    errors: list[str] = []
    from_index = with_email = 0
    from_search = updated = 0
    touched: list[uuid.UUID] = []
    for p in profiles:
        row = known.get(p.slug)
        hit = found.get(p.slug)
        first, last = (
            (row.first_name or p.first, row.last_name or p.last) if row else (p.first, p.last)
        )
        screen = on_screen is not None and hit is on_screen
        if row is None and hit is not None and not screen:
            from_search += 1
        if (row is None or screen) and hit is not None and hit.name:
            from orkesta.engine.normalize.names import split_full_name

            split = split_full_name(hit.name)
            first, last = split.first or first, split.last or last
        email = ""
        if row is not None and row.email and row.email_status in ("publicado", "confirmado"):
            email = row.email
        r_country, r_title, r_company = (
            (row.country or "", row.title or "", row.company or "") if row else ("", "", "")
        )
        h_country, h_title, h_company, h_city = (
            (hit.country, hit.role, hit.employer, hit.city) if hit else ("", "", "", "")
        )
        if screen:  # lo que se ve en pantalla manda
            found_country = h_country or r_country or code
            title, firm, city = h_title or r_title, h_company or r_company or company, h_city
        else:
            found_country = r_country or h_country or code
            title = r_title if row else h_title
            firm = r_company or h_company or company
            city = h_city if not row else ""
        data = ContactInput(
            first_name=first,
            last_name=last,
            job_title=title,
            company=firm,
            email=email,
            city=city,
            country=COUNTRIES.get(found_country, ("",))[0],
        )
        # Ya se agregó antes por su dirección: se completa lo que faltaba, sin duplicarlo.
        if session.scalar(select(Contact.id).where(Contact.tenant_id == tenant_id,
                                                   Contact.linkedin_url == p.url)):  # fmt: skip
            existing += 1
            if (refreshed := _refresh(session, tenant_id, p.url, data)) is not None:
                updated += 1
                touched.append(refreshed)
            continue
        try:
            contact = create_contact(session, tenant_id=tenant_id, user=user, data=data)
        except DataError as exc:
            if exc.status == 409:
                existing += 1
            else:
                errors.append(f"{p.first} {p.last}: {exc}"[:160])
            continue
        contact.linkedin_url = p.url[:300]
        if row is not None:
            from_index += 1
            contact.city = contact.city or (row.location or "").split(",")[0][:120] or None
        if email:
            with_email += 1
            contact.email_status = "sin_validar"
            contact.email_check_reason = (
                "Correo confirmado por una respuesta" if row and row.email_status == "confirmado"
                else "Correo publicado (índice de personas de ORKESTA)"
            )  # fmt: skip
        created.append(contact.id)
    session.flush()
    if list_id and created:
        add_to_list(session, tenant_id=tenant_id, list_id=list_id, contact_ids=created)
    pending = [
        cid for cid in [*created, *touched]
        if (c := session.get(Contact, cid)) is not None and not c.email and c.company_name
    ]  # fmt: skip
    no_company = sum(
        1 for cid in created if (c := session.get(Contact, cid)) is not None and not c.company_name
    )
    return {
        "received": len(profiles), "created": len(created), "existing": existing,
        "updated": updated,
        "from_index": from_index, "from_search": from_search, "search_off": not _search_on(),
        "search_error": search_error,
        "with_email": with_email, "to_enrich": pending,
        "no_company": no_company, "invalid": invalid[:20], "errors": errors[:20],
        "contact_ids": [str(c) for c in created],
    }  # fmt: skip
