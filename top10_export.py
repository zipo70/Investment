"""
top10_export.py — kører Top 10-scanningen og gemmer resultatet i repoet.

Køres af .github/workflows/top10_export.yml (GitHub har Yahoo-adgang, det
har Claudes miljø ikke). Resultatet committes til output/top10.json og
output/top10.md, så Claude kan læse det via git og analysere kandidaterne.

Ingen ny logik: genbruger core.find_top_candidates() — samme formel som
appens Top 10-fane (komposit 24/20/36/20, KØB/HOLD/SÆLG, GEICO-profil som
info). Research-værktøj, ikke finansiel rådgivning.
"""

import datetime
import json
import math
import os

from core import find_top_candidates

OUT_DIR = "output"


def _num(x, nd=1):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, nd)


def _row(c: dict) -> dict:
    fa = c.get("fundamental") or {}
    geico = c.get("geico") or {}
    ag = c.get("aktieguld") or {}
    plan = c.get("plan") or {}
    rec = c.get("recommendation") or {}
    meta = c.get("meta") or {}
    return {
        "ticker": c["ticker"],
        "navn": c.get("short_name"),
        "region": c.get("region"),
        "valuta": meta.get("currency"),
        "anbefaling": rec.get("action"),
        "komposit": _num(c.get("composite_score")),
        "ta_score": _num(c["ta"].get("ta_score")),
        "fundamental_score": _num(fa.get("fundamental_score")) if fa.get("ok") else None,
        "popularitet": _num(c.get("popularity_score")),
        "aktieguld": _num(ag.get("aktieguld_score")) if ag.get("ok") else None,
        "geico_score": _num(geico.get("geico_score")) if geico.get("ok") else None,
        "geico_label": geico.get("label") if geico.get("ok") else None,
        "geico_triggers": geico.get("geico_triggers", []),
        "upside_pct": _num(c.get("upside_pct")),
        "kurs": _num(plan.get("entry_price"), 2),
        "stop": _num(plan.get("stop_price"), 2),
        "stop_pct": _num(plan.get("stop_pct")),
        "stop_kilde": plan.get("stop_kilde"),
        "sell_score": rec.get("sell_score"),
        "sell_triggers": rec.get("sell_triggers", []),
        "naeste_regnskab": fa.get("next_earnings_date"),
        "ta_triggers": c["ta"].get("ta_triggers", []),
    }


def _fmt(v, suffix=""):
    return "–" if v is None else f"{v}{suffix}"


def main():
    started = datetime.datetime.now(datetime.timezone.utc)
    print("Kører Top 10-scanning...")
    candidates, stats = find_top_candidates(top_n=10)
    rows = [_row(c) for c in candidates]

    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "genereret_utc": started.isoformat(timespec="seconds"),
        "stats": stats,
        "kandidater": rows,
    }
    with open(os.path.join(OUT_DIR, "top10.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    lines = [
        f"# Top 10 — {started:%Y-%m-%d %H:%M} UTC",
        "",
        f"Univers: {stats.get('universe_size')} · data-fejl: {stats.get('skipped_data')} · "
        f"uden optrend: {stats.get('skipped_trend')} · shortlist: {stats.get('shortlist_size')}"
        + (" · ⚠️ mange data-fejl (rate-limit?)" if stats.get("high_skip_rate") else ""),
        "",
        "| # | Ticker | Navn | Anb. | Komposit | GEICO | Upside | Kurs | Stop | Regnskab |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['ticker']} | {r['navn'] or ''} | {r['anbefaling']} | {_fmt(r['komposit'])} | "
            f"{_fmt(r['geico_score'])} | {_fmt(r['upside_pct'], '%')} | {_fmt(r['kurs'])} | "
            f"{_fmt(r['stop'])} ({_fmt(r['stop_pct'], '%')}) | {r['naeste_regnskab'] or '–'} |"
        )
    lines += ["", "Research-værktøj, ikke finansiel rådgivning."]
    with open(os.path.join(OUT_DIR, "top10.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
