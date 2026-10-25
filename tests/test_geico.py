"""
tests/test_geico.py — Unit-tests af GEICO-profilen (core.compute_geico_profile).

INGEN netværksadgang — funktionen arbejder kun på allerede hentede data.
Kør med:  python tests/test_geico.py   (eller `pytest tests/`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import compute_geico_profile, GEICO_STRONG_THRESHOLD


def _fa(**kw):
    base = {"ok": True}
    base.update(kw)
    return base


# Et "1951-GEICO": effektiv, voksende, solid, ejerledet, billig og overset.
GEICO_LIKE = (
    _fa(roa=0.12, roe=0.25, revenue_growth=0.18, debt_to_equity=10,
        fcf_yield_pct=9.0, insiders_pct=0.15, forward_pe=9.0),
    {"ok": True, "num_analysts": 2},
    {"ok": True, "pct_from_period_high": -45.0},
)

# Populær, dyr momentum-aktie: høj kvalitet, men hverken billig eller overset.
POPULAR_EXPENSIVE = (
    _fa(roa=0.10, roe=0.30, revenue_growth=0.08, debt_to_equity=40,
        fcf_yield_pct=1.5, insiders_pct=0.001, forward_pe=45.0),
    {"ok": True, "num_analysts": 35},
    {"ok": True, "pct_from_period_high": -1.0},
)


def test_geico_like_scores_strong():
    r = compute_geico_profile(*GEICO_LIKE)
    assert r["ok"]
    assert r["pillars_used"] == 6
    assert r["geico_score"] >= GEICO_STRONG_THRESHOLD
    assert r["label"] == "Stærk GEICO-profil"
    assert any("Overset" in t for t in r["geico_triggers"])


def test_popular_expensive_scores_lower():
    strong = compute_geico_profile(*GEICO_LIKE)["geico_score"]
    weak = compute_geico_profile(*POPULAR_EXPENSIVE)
    assert weak["ok"]
    assert weak["geico_score"] < strong
    assert weak["pillars"]["Billig"] < 30
    assert weak["pillars"]["Overset"] < 10


def test_scores_stay_in_range():
    extreme = (
        _fa(roa=5.0, roe=-3.0, revenue_growth=-0.9, debt_to_equity=9999,
            fcf_yield_pct=-50, insiders_pct=0.99, forward_pe=0.5),
        {"ok": True, "num_analysts": 0},
        {"ok": True, "pct_from_period_high": -99.0},
    )
    r = compute_geico_profile(*extreme)
    assert r["ok"]
    for name, v in r["pillars"].items():
        assert 0.0 <= v <= 100.0, (name, v)
    assert 0.0 <= r["geico_score"] <= 100.0


def test_missing_data_is_skipped_not_punished():
    full = compute_geico_profile(*GEICO_LIKE)
    partial = compute_geico_profile(
        _fa(roa=0.12, roe=0.25, revenue_growth=0.18, debt_to_equity=10, fcf_yield_pct=9.0),
        {"ok": False}, {"ok": False},
    )
    assert partial["ok"]
    assert "Overset" not in partial["pillars"]
    assert "Ejerledelse" not in partial["pillars"]
    assert abs(partial["pillars"]["Effektivitet"] - full["pillars"]["Effektivitet"]) < 1e-9


def test_too_few_pillars_returns_not_ok():
    r = compute_geico_profile(_fa(revenue_growth=0.1), {"ok": False}, {"ok": False})
    assert not r["ok"]
    assert "for få nøgletal" in r["reason"]


def test_failed_fundamental_is_ignored():
    r = compute_geico_profile({"ok": False, "roa": 0.5}, {"ok": False}, {"ok": False})
    assert not r["ok"]


def test_negative_forward_pe_is_ignored():
    r = compute_geico_profile(
        _fa(roa=0.05, roe=0.10, revenue_growth=0.05, debt_to_equity=30, forward_pe=-12.0),
        {"ok": False}, {"ok": False},
    )
    assert r["ok"]
    assert "Billig" not in r["pillars"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} tests bestået")
