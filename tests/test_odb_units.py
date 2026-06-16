"""Regression tests for ODB++ unit detection edge cases."""

from pathlib import Path

import pytest

from odb.archive import parse_step_repeat
from odb.features import parse_features_text


def test_parse_features_detects_inline_outline_inches_without_header():
    # No #UNITS header, but OB/OS carry trailing "I" unit tokens.
    # Parser should still detect inches and return uf=25.4.
    txt = "\n".join([
        "OB -0.856299212598 -0.738188976378 I",
        "OS -0.856299212598  0.738188976378",
        "OS  0.856299212598  0.738188976378",
        "OS  0.856299212598 -0.738188976378",
        "OE",
    ])

    geoms, widths, warnings, fiducials, drills, detected_uf = parse_features_text(
        txt,
        uf=1.0,
        unknown_symbols=set(),
    )

    assert detected_uf == 25.4
    assert any("units override" in w for w in warnings)


def test_parse_step_repeat_uses_stephdr_units_override(tmp_path: Path):
    # Global uf=1.0 (mm) is intentionally wrong here.
    # stephdr declares inches, so parser must apply 25.4.
    job_root = tmp_path / "job"
    step_dir = job_root / "steps" / "panel"
    step_dir.mkdir(parents=True)

    stephdr = "\n".join([
        "UNITS=INCH",
        "STEP-REPEAT",
        "NAME=unit",
        "X=1.0",
        "Y=2.0",
        "DX=3.0",
        "DY=4.0",
        "NX=2",
        "NY=1",
        "}",
    ])
    (step_dir / "stephdr").write_text(stephdr, encoding="utf-8")

    parsed = parse_step_repeat(str(job_root), uf=1.0)
    assert "panel" in parsed
    sr = parsed["panel"][0]

    assert sr.x == pytest.approx(25.4)
    assert sr.y == pytest.approx(50.8)
    assert sr.dx == pytest.approx(76.2)
    assert sr.dy == pytest.approx(101.6)
