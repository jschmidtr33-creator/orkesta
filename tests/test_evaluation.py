"""Evaluación de dominios con red simulada: carga, métricas e informe."""

from __future__ import annotations

from pathlib import Path

from fakes import FakeFetcher, FakeResolver, page
from orkesta.enrichment.service import EngineDeps
from orkesta.evaluation.domains import baseline, load_cases, run, write_report

IP = ["93.184.216.34"]


def _dataset(tmp_path: Path) -> Path:
    path = tmp_path / "eval.csv"
    path.write_text(
        "empresa,ubicacion,dominio_esperado,fuente\n"
        'Lácteos Inventados,"Ciudad de México, México",lacteosinventados.com.mx|li.mx,prueba\n'
        'Empresa Fantasma,"Lima, Perú",fantasma.pe,prueba\n'
        'Sin Etiqueta Demo,"Monterrey, México",,pendiente\n',
        encoding="utf-8",
    )
    return path


def _engine() -> EngineDeps:
    resolver = FakeResolver(ips={"lacteosinventados.com.mx": IP, "sinetiquetademo.com.mx": IP})
    fetcher = FakeFetcher(
        pages={
            "https://lacteosinventados.com.mx/": page(
                "https://lacteosinventados.com.mx/",
                "<title>Lácteos Inventados</title><body>México +52</body>",
            ),
            "https://sinetiquetademo.com.mx/": page(
                "https://sinetiquetademo.com.mx/", "<title>Sin Etiqueta Demo</title>"
            ),
        }
    )
    return EngineDeps(resolver, fetcher, None)


def test_load_cases_normalizes_expected_domains(tmp_path: Path) -> None:
    cases = load_cases(_dataset(tmp_path))
    assert cases[0].expected == ("lacteosinventados.com.mx", "li.mx")
    assert cases[2].expected == ()


def test_baseline_is_name_plus_com(tmp_path: Path) -> None:
    assert baseline(load_cases(_dataset(tmp_path))[0]) == "lacteosinventados.com"


def test_metrics_and_report(tmp_path: Path) -> None:
    outcomes = run(load_cases(_dataset(tmp_path)), _engine, workers=2)
    metrics = write_report(outcomes, tmp_path / "out")
    assert metrics["empresas"] == 3 and metrics["etiquetadas"] == 2
    assert metrics["con_dominio"] == 1 and metrics["aciertos"] == 1
    assert metrics["exactitud_pct"] == 100.0 and metrics["cobertura_pct"] == 50.0
    assert metrics["linea_base_exactitud_pct"] == 0.0
    report = (tmp_path / "out" / "reporte.md").read_text(encoding="utf-8")
    assert "Empresa Fantasma" in report and "Sin Etiqueta Demo" in report
    assert (tmp_path / "out" / "resultados.csv").exists()
