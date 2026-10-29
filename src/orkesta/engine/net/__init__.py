"""Acceso a la red del motor: DNS, descarga segura de páginas y utilidades de dominio.

Todo el acceso externo pasa por las interfaces ``Resolver`` y ``Fetcher`` para que el motor
pueda probarse sin internet y para concentrar en un solo lugar las protecciones (SSRF,
robots.txt, límites de tamaño y tiempo).
"""
