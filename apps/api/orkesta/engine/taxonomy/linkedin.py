"""Filtros de búsqueda de leads con la estructura de LinkedIn Sales Navigator.

- ``SECTORS``: las industrias de LinkedIn agrupadas (con su nombre en español de LinkedIn), cada
  una con términos cortos para el buscador y su(s) industria(s) internas de ORKESTA
  (``industries.INDUSTRIES``) para clasificar empresas.
- ``FUNCTIONS``: las funciones de Sales Navigator.
- ``SENIORITY``: los niveles de responsabilidad de Sales Navigator.
- ``HEADCOUNT``: la plantilla de la empresa.
"""

from __future__ import annotations

import re
from functools import lru_cache

from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy.titles import classify_title

# (grupo, industria interna por defecto, ((nombre en LinkedIn, términos "a; b", internas), ...))
_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    ("Financieros y consultoría", "banca_finanzas", (
        ("Banca", "banco; bank; banca", ""),
        ("Banca de inversión", '"banca de inversión"; "investment banking"', ""),
        ("Asesoría de inversión", '"asesoría de inversiones"; "wealth management"', ""),
        ("Fondos de pensiones", '"fondo de pensiones"; pensiones; AFP', ""),
        ("Mercados de valores y de materias primas", '"bolsa de valores"; "comisionista de bolsa"; commodities', ""),
        ("Gestión de inversiones", '"gestión de inversiones"; "asset management"; fiduciaria', ""),
        ("Mercados de capitales", '"mercado de capitales"; "capital markets"', ""),
        ("Aseguradoras", "aseguradora; insurance", "seguros"),
        ("Seguros", "seguros; insurance", "seguros"),
        ("Entidades de capital riesgo y capital privado", '"private equity"; "venture capital"; "capital privado"', ""),
        ("Servicios financieros", '"servicios financieros"; "financial services"; financiera', ""),
        ("Intermediación crediticia", "crédito; microfinanzas; lending", ""),
        ("Servicios de información", '"servicios de información"; "information services"', "tecnologia_software"),
        ("Consultoría de operaciones", '"consultoría de operaciones"; "operations consulting"', "servicios_profesionales"),
        ("Consultoría y servicios a empresas", "consultoría; consulting; consultora", "servicios_profesionales"),
    )),
    ("Tecnología", "tecnologia_software", (
        ("Tecnología, información e internet", "tecnología; technology; internet", ""),
        ("Desarrollo de software", "software; desarrollo de software; developer", ""),
        ("Servicios y consultoría de TI", '"servicios de TI"; "IT services"; "consultoría TI"', ""),
        ("Servicios de TI", '"servicios de TI"; "IT services"; outsourcing TI', ""),
        ("Software como servicio (SaaS)", "SaaS; software; plataforma", ""),
        ("Ciberseguridad", "ciberseguridad; cybersecurity; seguridad informática", ""),
        ("Seguridad informática y de redes", '"seguridad informática"; "network security"; ciberseguridad', ""),
        ("Internet", "internet; digital; online", ""),
        ("Comercio electrónico", '"comercio electrónico"; ecommerce; marketplace', "tecnologia_software retail_consumo"),
        ("Servicios de datos e infraestructura", '"data center"; cloud; nube', ""),
        ("Computación en la nube", "cloud; nube; AWS; Azure", ""),
        ("Inteligencia artificial", '"inteligencia artificial"; IA; AI; "machine learning"', ""),
        ("Análisis de datos y big data", '"big data"; analytics; datos', ""),
        ("Tecnología financiera (fintech)", "fintech; pagos; payments", "tecnologia_software banca_finanzas"),
        ("Juegos de ordenador y videojuegos", "videojuegos; gaming; games", "tecnologia_software medios_entretenimiento"),
        ("Hardware y equipos informáticos", "hardware; computadores; equipos", "tecnologia_software manufactura_industrial"),
        ("Telecomunicaciones e IoT", "IoT; conectividad; telemetría", "tecnologia_software telecomunicaciones"),
        ("Tecnología educativa (edtech)", "edtech; e-learning; plataforma educativa", "tecnologia_software educacion"),
        ("Tecnología sanitaria (healthtech)", "healthtech; salud digital; telemedicina", "tecnologia_software farmaceutica_salud"),
    )),
    ("Manufactura e industria", "manufactura_industrial", (
        ("Fabricación de artículos de deporte", '"artículos deportivos"; "sporting goods"', ""),
        ("Fabricación de productos de defensa y espacio", "defensa; aeroespacial; defense", ""),
        ("Fabricación de equipos informáticos", "hardware; equipos informáticos", "manufactura_industrial tecnologia_software"),
        ("Fabricación de ordenadores, periféricos y artículos electrónicos", "electrónica; electronics; computadores", "manufactura_industrial tecnologia_software"),
        ("Fabricación de semiconductores", "semiconductores; semiconductors; chips", "manufactura_industrial tecnologia_software"),
        ("Fabricación de muebles y artículos para el hogar", "muebles; furniture; hogar", "manufactura_industrial retail_consumo"),
        ("Fabricación de componentes aeroespaciales y de aviación", "aeroespacial; aerospace; aviación", "manufactura_industrial transporte_logistica"),
        ("Fabricación de equipo médico", '"dispositivos médicos"; "medical devices"; "equipo médico"', "manufactura_industrial farmaceutica_salud"),
        ("Fabricación de componentes, piezas y accesorios para vehículos de motor", 'autopartes; "auto parts"; automotriz', "manufactura_industrial automotriz"),
        ("Fabricación de maquinaria", "maquinaria; machinery; equipos industriales", ""),
        ("Fabricación de productos químicos", "química; chemicals; químicos", "quimica_materiales"),
        ("Fabricación de equipos eléctricos", '"equipos eléctricos"; "electrical equipment"', ""),
        ("Fabricación de productos textiles", "textil; textiles; confección", "textil_moda"),
        ("Fabricación de papel y productos forestales", "papel; cartón; forestal", "quimica_materiales"),
        ("Fabricación de electrodomésticos y productos eléctricos y electrónicos", "electrodomésticos; appliances; electrónica", "manufactura_industrial retail_consumo"),
        ("Fabricación de productos de plástico", "plásticos; plastics; empaques", "quimica_materiales"),
        ("Ingeniería industrial o mecánica", '"ingeniería industrial"; "ingeniería mecánica"; "mechanical engineering"', ""),
        ("Materiales de construcción", '"materiales de construcción"; cemento; acero', "construccion_inmobiliario quimica_materiales"),
        ("Industria textil y moda", "textil; moda; fashion", "textil_moda"),
        ("Industria manufacturera", "manufactura; manufacturing; planta", ""),
    )),
    ("Retail y consumo", "retail_consumo", (
        ("Comercio al por menor de prendas de vestir y moda", "moda; ropa; fashion retail", "retail_consumo textil_moda"),
        ("Comercio al por menor de comestibles", "supermercado; supermercados; grocery", ""),
        ("Comercio al por menor", "retail; tiendas; minorista", ""),
        ("Comercio al por mayor de materiales de construcción", '"materiales de construcción"; ferretería; mayorista', "retail_consumo construccion_inmobiliario"),
        ("Servicios de alimentos y bebidas", "restaurantes; restaurante; foodservice", "alimentos_bebidas"),
        ("Alimentación y bebidas", "alimentos; bebidas; food", "alimentos_bebidas"),
        ("Farmacias minoristas", "farmacia; droguería; pharmacy", "retail_consumo farmaceutica_salud"),
        ("Lácteos", "lácteos; dairy; leche", "alimentos_bebidas"),
        ("Fabricación de productos lácteos", "lácteos; dairy; quesos", "alimentos_bebidas"),
        ("Agricultura", "agrícola; agricultura; agro", "agroindustria"),
        ("Ganadería", "ganadería; ganadera; livestock", "agroindustria"),
        ("Producción alimentaria", 'alimentos; "food production"; agroindustria', "alimentos_bebidas agroindustria"),
        ("Fabricación de alimentos y bebidas", 'alimentos; bebidas; "consumo masivo"', "alimentos_bebidas"),
        ("Fabricación de productos farmacéuticos", "farmacéutica; pharma; laboratorio", "farmaceutica_salud"),
        ("Fabricación de productos de cuidado personal", '"cuidado personal"; "personal care"; higiene', "farmaceutica_salud retail_consumo"),
        ("Cosmética", "cosmética; cosméticos; belleza", "farmaceutica_salud retail_consumo"),
        ("Fabricación de tabaco", "tabaco; tobacco; tabacalera", "alimentos_bebidas"),
    )),
    ("Telecomunicaciones", "telecomunicaciones", (
        ("Telecomunicaciones", "telecomunicaciones; telecom; telco", ""),
        ("Productos de redes informáticas", "redes; networking; conectividad", "telecomunicaciones tecnologia_software"),
        ("Operadores de telecomunicaciones", "operador; telecomunicaciones; móvil", ""),
        ("Medios de comunicación y telecomunicaciones", "medios; telecomunicaciones; media", "telecomunicaciones medios_entretenimiento"),
        ("Centros de atención telefónica", '"call center"; "contact center"; BPO', "servicios_profesionales"),
    )),
    ("Utilities y minería", "energia", (
        ("Energía renovable y medio ambiente", '"energía renovable"; renewables; sostenibilidad', ""),
        ("Petróleo y gas", 'petróleo; "oil & gas"; hidrocarburos', "mineria_petroleo"),
        ("Extracción de petróleo", "petróleo; petrolera; upstream", "mineria_petroleo"),
        ("Extracción de gas natural", '"gas natural"; "natural gas"; gas', "mineria_petroleo"),
        ("Minería", "minería; mining; minera", "mineria_petroleo"),
        ("Petróleo, gas y minería", "petróleo; minería; energía", "mineria_petroleo"),
        ("Servicios públicos", '"servicios públicos"; utilities; acueducto', ""),
        ("Administración de servicios públicos", '"servicios públicos"; utilities; agua', ""),
        ("Generación de energía eléctrica", '"energía eléctrica"; generadora; power', ""),
        ("Generación de energía renovable", '"energía renovable"; solar; eólica', ""),
        ("Recogida de residuos", "residuos; reciclaje; aseo", "energia servicios_profesionales"),
        ("Tratamiento y eliminación de residuos", 'residuos; "waste management"; reciclaje', "energia servicios_profesionales"),
        ("Generación de energía eléctrica eólica", 'eólica; "wind energy"; renovable', ""),
    )),
    ("Logística, transporte y automotriz", "transporte_logistica", (
        ("Transporte de mercancías y paquetes", "carga; mensajería; courier", ""),
        ("Aeronáutica y aviación", "aviación; aerolínea; aviation", ""),
        ("Transporte, logística, cadena de suministro y almacenamiento", 'logística; "supply chain"; transporte', ""),
        ("Transporte por ferrocarril", "ferrocarril; férreo; railway", ""),
        ("Transporte terrestre de pasajeros", "transporte; pasajeros; buses", ""),
        ("Servicios postales", "postal; correo; paquetería", ""),
        ("Sector automovilístico", "automotriz; automotive; concesionario", "automotriz"),
        ("Fabricación de vehículos de motor", "vehículos; ensambladora; automotriz", "automotriz manufactura_industrial"),
        ("Fabricación de equipos de transporte", '"equipos de transporte"; carrocerías; vehículos', "automotriz manufactura_industrial"),
        ("Industria aeroespacial y aviación", "aeroespacial; aviación; aerospace", "transporte_logistica manufactura_industrial"),
    )),
    ("Salud y hospitales", "farmaceutica_salud", (
        ("Hospitales y atención sanitaria", "hospital; clínica; salud", ""),
        ("Consultorios médicos", "consultorio; médico; IPS", ""),
        ("Laboratorios médicos y de diagnóstico", "laboratorio; diagnóstico; clínico", ""),
        ("Servicios médicos", '"servicios médicos"; salud; EPS', ""),
        ("Hospitales", "hospital; hospitales; clínica", ""),
        ("Salud y servicios sociales", 'salud; "servicios sociales"; bienestar', ""),
        ("Salud pública", '"salud pública"; "public health"; secretaría de salud', "farmaceutica_salud gobierno"),
    )),
    ("Entretenimiento y turismo", "medios_entretenimiento", (
        ("Cine, vídeo y sonido", "cine; audiovisual; producción", ""),
        ("Proveedores de entretenimiento", "entretenimiento; entertainment; espectáculos", ""),
        ("Casinos y centros de apuestas", "casino; apuestas; gaming", ""),
        ("Organización de viajes", '"agencia de viajes"; viajes; travel', "turismo_hoteleria"),
        ("Hostelería", "hotelería; hospitality; hotel", "turismo_hoteleria"),
        ("Producción y distribución audiovisual", "audiovisual; productora; contenidos", ""),
        ("Instalaciones recreativas", "recreación; parque; club", "medios_entretenimiento turismo_hoteleria"),
        ("Producción multimedia", "multimedia; contenidos; producción digital", ""),
        ("Hoteles y moteles", "hotel; hoteles; resort", "turismo_hoteleria"),
        ("Servicios de eventos", "eventos; events; ferias", ""),
        ("Ocio, viajes y turismo", "turismo; viajes; tourism", "turismo_hoteleria"),
    )),
    ("Construcción y bienes raíces", "construccion_inmobiliario", (
        ("Bienes inmuebles", 'inmobiliaria; "bienes raíces"; "real estate"', ""),
        ("Construcción", "construcción; constructora; construction", ""),
        ("Ingeniería civil", '"ingeniería civil"; obras; infraestructura', ""),
        ("Arquitectura y planificación", "arquitectura; architecture; urbanismo", ""),
        ("Diseño de interiores", '"diseño de interiores"; "interior design"; interiorismo', ""),
        ("Construcción de edificios", "edificios; constructora; vivienda", ""),
        ("Construcción naval", "astillero; naval; shipbuilding", "construccion_inmobiliario manufactura_industrial"),
        ("Bienes inmobiliarios comerciales", '"real estate comercial"; "centros comerciales"; inmobiliaria', ""),
    )),
    ("Educación", "educacion", (
        ("Educación", "educación; education; colegio", ""),
        ("Formación técnica y profesional", '"formación técnica"; instituto; técnico', ""),
        ("Enseñanza superior", 'universidad; university; "educación superior"', ""),
        ("Proveedores de e-learning", 'e-learning; edtech; "educación virtual"', "educacion tecnologia_software"),
        ("Formación profesional y coaching", "coaching; capacitación; training", ""),
    )),
    ("Servicios empresariales", "servicios_profesionales", (
        ("Servicios de ingeniería", '"servicios de ingeniería"; ingeniería; "engineering services"', ""),
        ("Servicios de investigación", "investigación; research; estudios", ""),
        ("Administración pública", 'gobierno; ministerio; "sector público"', "gobierno"),
        ("Servicios de publicidad", "publicidad; agencia; advertising", "medios_entretenimiento servicios_profesionales"),
        ("Servicios de impresión", "impresión; imprenta; printing", ""),
        ("Servicios medioambientales", "ambiental; environmental; sostenibilidad", ""),
        ("Servicios al consumidor", '"servicios al consumidor"; "consumer services"', "servicios_profesionales retail_consumo"),
        ("Servicios de gestión estratégica", 'estrategia; "management consulting"; consultoría', ""),
        ("Dotación y selección de personal", "selección; staffing; headhunter", ""),
        ("Servicios de marketing", "marketing; mercadeo; agencia", "servicios_profesionales medios_entretenimiento"),
        ("Recursos humanos", '"recursos humanos"; "human resources"; talento', ""),
        ("Plataformas de inteligencia empresarial", '"business intelligence"; BI; analytics', "servicios_profesionales tecnologia_software"),
        ("Servicios administrativos y auxiliares", "outsourcing; BPO; administrativos", ""),
        ("Desarrollo y comercio internacional", '"comercio exterior"; exportaciones; "international trade"', "servicios_profesionales ong"),
        ("Seguridad e investigación", '"seguridad privada"; vigilancia; security', ""),
        ("Servicios jurídicos", 'abogados; legal; "firma de abogados"', ""),
    )),
)  # fmt: skip


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(text)).strip("_")[:60]


def _terms(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in raw.split(";"):
        term = part.strip()
        if not term:
            continue
        # Frases de varias palabras entre comillas para que el buscador las tome juntas.
        if " " in term and not term.startswith('"'):
            term = f'"{term.strip(chr(34))}"'
        out.append(term)
    return tuple(out)


SECTORS: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
"""código → (nombre, grupo, términos de búsqueda, industrias internas)."""
SECTOR_GROUPS: list[tuple[str, list[str]]] = []
for _group, _default, _items in _GROUPS:
    _codes: list[str] = []
    for _label, _raw, _parents in _items:
        _code = _slug(_label)
        if _code in SECTORS:
            continue
        SECTORS[_code] = (_label, _group, _terms(_raw), tuple((_parents or _default).split()))
        _codes.append(_code)
    SECTOR_GROUPS.append((_group, _codes))


def sector_parents(codes: list[str]) -> list[str]:
    return list(dict.fromkeys(p for c in codes if c in SECTORS for p in SECTORS[c][3]))


def sector_terms(codes: list[str], limit: int = 8) -> list[str]:
    return list(dict.fromkeys(t for c in codes if c in SECTORS for t in SECTORS[c][2]))[:limit]


# --- Funciones de Sales Navigator ------------------------------------------------------------

# código → (nombre, términos de búsqueda, palabras del cargo, áreas internas de ORKESTA)
FUNCTIONS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "contabilidad": ("Contabilidad", ("contabilidad", "contador", "accounting"),
                     ("contab", "contador", "contadora", "accounting", "accountant", "tesorer"), ()),
    "administrativo": ("Administrativo", ("administrativo", "administración", '"office manager"'),
                       ("administrativ", "administracion", "office manager", "secretari"), ()),
    "arte_diseno": ("Arte y diseño", ("diseño", "design", "creativo"),
                    ("disen", "design", "creativ", "arte", "art director", "ux", "ui"), ()),
    "desarrollo_empresarial": ("Desarrollo empresarial",
                               ('"desarrollo de negocios"', '"business development"', "alianzas"),
                               ("desarrollo de negocio", "business development", "bdm", "alianza",
                                "partnership", "expansion", "new business"), ()),
    "servicios_sociales": ("Servicios sociales y comunitarios",
                           ('"trabajo social"', "comunidad", "sostenibilidad"),
                           ("social", "comunidad", "community", "rse", "sostenibilidad",
                            "sustainability"), ()),
    "consultoria": ("Consultoría", ("consultor", "consultoría", "consulting"),
                    ("consult", "asesor"), ()),
    "educacion": ("Educación", ("docente", "profesor", "educación"),
                  ("docente", "profesor", "profesora", "educacion", "education", "academic",
                   "rector", "rectora", "decano", "decana", "teacher"), ()),
    "ingenieria": ("Ingeniería", ("ingeniería", "ingeniero", "engineering"),
                   ("ingenier", "engineer"), ()),
    "emprendimiento": ("Emprendimiento", ("fundador", "emprendedor", "founder"),
                       ("fundador", "fundadora", "founder", "emprendedor", "emprendedora",
                        "entrepreneur", "cofundador", "cofundadora"), ()),
    "finanzas": ("Finanzas", ("finanzas", "financiero", "CFO"),
                 ("finanz", "financ", "cfo", "tesorer", "riesgo", "credito"), ("finanzas",)),
    "servicios_sanitarios": ("Servicios sanitarios", ("médico", "salud", "enfermería"),
                             ("medic", "salud", "health", "enfermer", "clinic", "odontolog"), ()),
    "recursos_humanos": ("Recursos humanos", ('"recursos humanos"', "talento", "RRHH"),
                         (), ("rrhh",)),
    "ti": ("Tecnología de la información", ("tecnología", "TI", "sistemas", "IT"),
           ("sistemas", 'software", "desarrollador", "developer', "infraestructura"),
           ("tecnologia", "seguridad", "datos", "transformacion_digital")),
    "legal": ("Legal", ("legal", "jurídico", "abogado"), ("abogad", "juridic"), ("legal",)),
    "marketing": ("Marketing", ("marketing", "mercadeo", "marca"), ("brand", "marca"),
                  ("marketing",)),
    "medios_comunicacion": ("Medios y comunicación", ("comunicaciones", "prensa", "communications"),
                            ("comunicacion", "communication", "prensa", "relaciones publicas",
                             "periodis", "contenido", "content", "pr"), ()),
    "militar_proteccion": ("Servicios militares y de protección",
                           ('"seguridad física"', "militar", "protección"),
                           ("militar", "military", "defensa", "proteccion", "vigilancia",
                            "seguridad fisica", "security officer", "escolta"), ()),
    "operaciones": ("Operaciones", ("operaciones", "operations", "COO"), (), ("operaciones",)),
    "gestion_productos": ("Gestión de productos", ('"product manager"', "producto", '"product owner"'),
                          ("product", "producto"), ()),
    "proyectos": ("Gestión de programas y proyectos", ("proyectos", "PMO", '"project manager"'),
                  ("proyecto", "project", "pmo", "programa", "program", "scrum"), ()),
    "compras": ("Compras", ("compras", "procurement", "abastecimiento"),
                ("abastecimiento", "sourcing"), ("compras", "supply_chain")),
    "calidad": ("Control de calidad", ("calidad", "quality", "QA"),
                ("calidad", "quality", "qa", "hse", "qhse", "aseguramiento"), ()),
    "inmobiliaria": ("Inmobiliaria", ("inmobiliaria", '"real estate"', '"bienes raíces"'),
                     ("inmobiliari", "real estate", "bienes raices", "property", "propiedad"), ()),
    "investigacion": ("Investigación", ("investigación", "research", '"I+D"'),
                      ("investigac", "research", "r&d", "i+d", "cientific", "scientist"), ()),
    "ventas": ("Ventas", ("ventas", "comercial", "sales"),
               ("ventas", "comercial", "sales", "kam", "account"), ("comercial",)),
    "atencion_cliente": ("Atención al cliente",
                         ('"servicio al cliente"', '"experiencia del cliente"', "CX"),
                         ("cliente", "customer", "cx", "soporte", "support"), ("atencion_cliente",)),
}  # fmt: skip


def _key_pattern(keys: tuple[str, ...]) -> re.Pattern[str] | None:
    if not keys:
        return None
    parts = [re.escape(k) + (r"s?\b" if len(k) <= 3 else "") for k in keys]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + ")")


_FUNCTION_PATTERNS = {code: _key_pattern(spec[2]) for code, spec in FUNCTIONS.items()}


@lru_cache(maxsize=20_000)
def title_functions(title: str) -> frozenset[str]:
    """Funciones de Sales Navigator que encajan con un cargo."""
    text = fold(title or "")
    _, area = classify_title(title)
    found = {
        code
        for code, (_, _, _, areas) in FUNCTIONS.items()
        if (area and area in areas)
        or ((p := _FUNCTION_PATTERNS[code]) is not None and p.search(text))
    }
    return frozenset(found)


def function_terms(codes: list[str], limit: int = 8) -> list[str]:
    return list(dict.fromkeys(t for c in codes if c in FUNCTIONS for t in FUNCTIONS[c][1]))[:limit]


def function_areas(codes: list[str]) -> list[str]:
    """Áreas internas equivalentes; vacío si alguna función no tiene equivalente."""
    areas: list[str] = []
    for code in codes:
        mapped = FUNCTIONS.get(code, ("", (), (), ()))[3]
        if not mapped:
            return []
        areas += [a for a in mapped if a not in areas]
    return areas


# --- Nivel de responsabilidad --------------------------------------------------------------

# código → (nombre, términos de búsqueda, niveles internos)
SENIORITY: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "propietario": ("Propietario / Socio", ("fundador", "socio", "founder", "owner", "propietario"),
                    ("duenio_socio",)),
    "cxo": ("Cargo directivo de nivel C (CXO)",
            ('"gerente general"', "CEO", "presidente", "CFO", "CTO", "COO", "CMO"), ("c_level",)),
    "vp": ("Vicepresidente", ("vicepresidente", "VP", '"vice president"'), ("vp",)),
    "director": ("Director", ("director", "directora", "head"), ("director",)),
    "gestion_experiencia": ("Experiencia en gestión", ("gerente", "manager", "subgerente"),
                            ("gerente",)),
    "gestion_inicial": ("Gestión inicial", ("jefe", "coordinador", "supervisor", "líder"),
                        ("jefe", "coordinador")),
    "senior": ("Sénior", ("senior", "especialista", "specialist", "consultor"), ("especialista",)),
    "sin_experiencia": ("Sin experiencia", ("junior", "analista", "asistente", "auxiliar"), ()),
    "practicas": ("En prácticas", ("practicante", "pasante", "intern", "becario"), ()),
}  # fmt: skip
DEFAULT_SENIORITY = ("propietario", "cxo", "vp", "director", "gestion_experiencia",
                     "gestion_inicial")  # fmt: skip
_LEVEL_TO_SENIORITY = {lv: code for code, (_, _, levels) in SENIORITY.items() for lv in levels}
_INTERN = re.compile(r"\b(?:practicante|pasante|intern|internship|becari[oa]|aprendiz|trainee)\b")
_JUNIOR = re.compile(r"\b(?:junior|jr|asistente|assistant|auxiliar|analista|analyst)\b")


@lru_cache(maxsize=20_000)
def title_seniority(title: str) -> str | None:
    """Nivel de responsabilidad de Sales Navigator para un cargo."""
    text = fold(title or "")
    if _INTERN.search(text):
        return "practicas"
    level, _ = classify_title(title)
    if level in _LEVEL_TO_SENIORITY and level != "especialista":
        return _LEVEL_TO_SENIORITY[level]
    if _JUNIOR.search(text) and not re.search(r"\b(?:senior|sr)\b", text):
        return "sin_experiencia"
    return "senior" if level == "especialista" else None


def seniority_levels(codes: list[str]) -> list[str]:
    return list(dict.fromkeys(lv for c in codes if c in SENIORITY for lv in SENIORITY[c][2]))


def seniority_terms(code: str) -> list[str]:
    return list(SENIORITY.get(code, ("", (), ()))[1])


# --- Plantilla de la empresa ----------------------------------------------------------------

HEADCOUNT: tuple[tuple[str, str], ...] = (
    ("1-10", "1-10"),
    ("11-50", "11-50"),
    ("51-200", "51-200"),
    ("201-500", "201-500"),
    ("501-1000", "501-1.000"),
    ("1001-5000", "1.001-5.000"),
    ("5001-10000", "5.001-10.000"),
    ("10001+", "Más de 10.000"),
)

PROFILE_LANGUAGES = {"es": "Español", "en": "Inglés", "pt": "Portugués"}
