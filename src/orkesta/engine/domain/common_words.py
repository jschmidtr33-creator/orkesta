"""Palabras comunes y nombres geográficos: como dominio aislado rara vez son de la empresa.

"Empresas Públicas de Medellín" no es medellin.com ni "Industria de Alimentos Zenú" es
alimentos.com: esos dominios pertenecen a cualquiera. Cuando la etiqueta del dominio es una de
estas palabras y el nombre de la empresa tiene más palabras, la coincidencia es débil.
"""

from __future__ import annotations

from orkesta.engine.normalize.location import CITIES, COUNTRIES, REGIONS

_WORDS = """
abogados academia accion acero aceros administracion aero agencia agricola agro agua aguas
aire alimentos aliados alianza almacen almacenes alpha ambiental america andes apoyo arte
asesores asesoria asesoramiento asociacion asociados atlantico auto autos azul banca banco
bebidas bien bienes bio blindajes boticas cafe caja calidad camara campo capital carga casa
centro centros ciencia ciencias cientifica cine ciudad clinica club colegio comercial
comercio comida compania comunicaciones confecciones constructora construcciones consultores
consultoria contacto control cooperativa corporacion cosmetics cosmeticos credito
cultura datos delta desarrollo digital directo diseno distribuidora distribuciones drogueria
editorial educacion educativa electrica electricos empresa empresas energia equipos escuela
estrategia estudio estudios express fabrica familia farmacia farmacias federacion fibra
finanzas financiera fondo fondos forma futuro gas general gestion global gold green grupo
group hacienda hogar home hospital hotel hoteles housing imagen industria industrial
industriales industrias infraestructura ingenieria innovacion instituto integral inversiones
investigacion jardin lab labs laboratorio latam legal libre life linea logistica lux maquinas
mar marca marina market marketing materiales media medica medicos medios mercado meta metal
metales minera mineria moda motor motores movil mundo municipal nacional natural negocios
norte nova nueva nuevo oficina omega oro pacifico papel parque partners plaza plataforma
plus point postales premium prime proyectos publicidad puerto puertos punto quimica quimicos
radio red redes salud seguridad seguros servicios sistemas social sociedad sol solar
soluciones solutions star studio sur systems taller tech tecnologia tecnologias telecom
textil textiles tienda tiendas trading transporte transportes travel turismo union
universal universidad urbano valle vida viajes vision world
"""

COMMON_WORDS: frozenset[str] = frozenset(
    _WORDS.split()
    + [key.replace(" ", "") for key in CITIES]
    + [key.replace(" ", "") for key in REGIONS]
    + [alias.replace(" ", "") for _, aliases in COUNTRIES.values() for alias in aliases]
)


def is_common_word(label: str) -> bool:
    return label.replace("-", "") in COMMON_WORDS
