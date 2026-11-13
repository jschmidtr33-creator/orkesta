"""Direcciones de imágenes: relativas dentro de ORKESTA, completas solo en los correos."""

from __future__ import annotations

from orkesta.core.assets import absolute, relative

ID = "0b5c1f0e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"


def test_old_domains_become_relative_and_emails_get_the_current_one() -> None:
    old = f'<img src="https://orkesta-viejo.onrender.com/t/i/{ID}.png"> y http://3-1-1-1.sslip.io/t/i/{ID}.jpg'
    assert relative(old) == f'<img src="/t/i/{ID}.png"> y /t/i/{ID}.jpg'
    assert relative("https://cdn.example.com/logo.png") == "https://cdn.example.com/logo.png"
    sent = absolute(old, "https://app.orkesta.co/")
    assert (
        sent
        == f'<img src="https://app.orkesta.co/t/i/{ID}.png"> y https://app.orkesta.co/t/i/{ID}.jpg'
    )
    assert absolute(f"/t/i/{ID}.gif", "") == f"/t/i/{ID}.gif"


def test_old_orkesta_links_point_to_the_current_domain() -> None:
    from orkesta.core.assets import current_links

    old = "Mira https://orkesta-web.onrender.com/app/p/abc y <a href='http://3-1-1-1.sslip.io/app/p/x'>"
    assert current_links(old, "https://orkestaapp.com/") == (
        "Mira https://orkestaapp.com/app/p/abc y <a href='https://orkestaapp.com/app/p/x'>"
    )
    assert current_links("https://render.com/docs", "https://orkestaapp.com") == (
        "https://render.com/docs"
    )
