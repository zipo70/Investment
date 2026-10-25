"""
validate_models.py — fuld validering af swing-modellerne (backtest_v3).

  1. Realisme-effekt: originalen som før vs. med næste-dags-åbning, DKK,
     vekselgebyr.
  2. Model-sammenligning: original momentum, sektorloft, 10 aktier,
     12-1 momentum, appens tekniske model (20-dages hold og kvartalsvis),
     momentum med appens filter.
  3. Træning 2015–2022 / test 2023→ / hele perioden, 13 startuger pr. model.
  4. Benchmarks: ACWI og MTUM (momentum-ETF), i DKK.
  5. Robusthed for de to bedste i træningen: 30 tilfældige 70 %-udsnit af
     universet + kørsel uden de 5 aktier der bidrog mest.

Output: output/validate_models.md og .json
Research-værktøj, ikke finansiel rådgivning.
"""

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from universe import full_universe, BENCHMARK
from backtest_v3 import Data, Model, Costs, run, stats

PERIODS = {"Træning 2015–2022": ("2015-01-02", "2022-12-31"),
           "Test 2023→": ("2023-01-02", None),
           "Hele perioden": ("2015-01-02", None)}
OFFSETS = range(13)

MODELS = [
    Model("A0. Original som før (samme dag, lokal valuta)", realistic=False),
    Model("A. Original realistisk"),
    Model("B. Original + sektorloft (maks 2)", sector_cap=2),
    Model("C. 10 aktier + sektorloft (maks 3)", n=10, sector_cap=3),
    Model("D. 12-1 momentum + sektorloft", score="mom12", sector_cap=2),
    Model("E. App-model, 20 dages hold", score="tech", rebalance_weeks=None, stop=0.10,
          entry_filter=True, live_exit=True, max_hold_weeks=4),
    Model("F. App-model, kvartalsvis", score="tech", stop=0.10, entry_filter=True, live_exit=True),
    Model("G. Momentum + app-filter + sektorloft", entry_filter=True, live_exit=True, sector_cap=2),
]


def evaluate(data, m, costs, start, end, allowed=None, offsets=OFFSETS):
    rows = []
    for o in offsets:
        r = run(data, m, costs, start, end, o, allowed)
        s, a, mt = stats(r["equity"]), stats(r["ACWI"]), stats(r.get("MTUM", r["ACWI"]))
        rows.append({"cagr": s["cagr"], "dd": s["dd"], "sharpe": s["sharpe"],
                     "acwi": a["cagr"], "acwi_dd": a["dd"], "acwi_sh": a["sharpe"],
                     "mtum": mt["cagr"], "mtum_dd": mt["dd"], "mtum_sh": mt["sharpe"],
                     "trades": len(r["trades"]), "fees": r["fees"]})
    df = pd.DataFrame(rows)
    q = lambda c, p: round(float(df[c].quantile(p)), 1)
    return {"cagr": q("cagr", .5), "cagr_p25": q("cagr", .25), "cagr_p75": q("cagr", .75),
            "dd": q("dd", .5), "sharpe": round(float(df.sharpe.median()), 2),
            "acwi": q("acwi", .5), "acwi_dd": q("acwi_dd", .5), "acwi_sh": round(float(df.acwi_sh.median()), 2),
            "mtum": q("mtum", .5), "mtum_dd": q("mtum_dd", .5), "mtum_sh": round(float(df.mtum_sh.median()), 2),
            "slaar_acwi": round(float((df.cagr > df.acwi).mean() * 100)),
            "slaar_mtum": round(float((df.cagr > df.mtum).mean() * 100)),
            "handler": int(df.trades.median()), "gebyrer_dkk": round(float(df.fees.median()))}


def main():
    costs = Costs()
    data = Data([t for t in full_universe() if t != BENCHMARK])
    data.build_signals()

    res = {}
    for m in MODELS:
        res[m.name] = {p: evaluate(data, m, costs, *rng) for p, rng in PERIODS.items()}
        print(m.name, res[m.name])

    # Robusthed for de to bedste realistiske modeller i TRÆNINGEN (Sharpe)
    cands = [m for m in MODELS if m.realistic]
    best = sorted(cands, key=lambda m: res[m.name]["Træning 2015–2022"]["sharpe"], reverse=True)[:2]
    rng = np.random.default_rng(42)
    robust = {}
    for m in best:
        boot = []
        for _ in range(30):
            sub = set(rng.choice(data.tickers, size=int(len(data.tickers) * 0.7), replace=False))
            r = run(data, m, costs, "2015-01-02", None, 0, sub)
            s, a = stats(r["equity"]), stats(r["ACWI"])
            boot.append({"cagr": s["cagr"], "sharpe": s["sharpe"], "acwi": a["cagr"]})
        b = pd.DataFrame(boot)
        full = run(data, m, costs, "2015-01-02", None, 0)
        contrib = pd.DataFrame(full["trades"]).groupby("ticker")["pnl_dkk"].sum().sort_values(ascending=False)
        top5 = list(contrib.head(5).index)
        ex = evaluate(data, m, costs, "2015-01-02", None, set(data.tickers) - set(top5))
        robust[m.name] = {
            "bootstrap_cagr_p10": round(float(b.cagr.quantile(.1)), 1),
            "bootstrap_cagr_median": round(float(b.cagr.median()), 1),
            "bootstrap_cagr_p90": round(float(b.cagr.quantile(.9)), 1),
            "bootstrap_slaar_acwi_pct": round(float((b.cagr > b.acwi).mean() * 100)),
            "top5_bidrag": {t: round(float(contrib[t])) for t in top5},
            "uden_top5": ex,
        }
        print("robusthed", m.name, robust[m.name])

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "omkostninger": {"kurtage": costs.cost_pct, "min_kurtage_dkk": costs.min_fee, "vekselgebyr": costs.fx_fee},
           "sektorer": data.sector, "resultater": res, "robusthed": robust}
    os.makedirs("output", exist_ok=True)
    with open("output/validate_models.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# Modelvalidering — {out['genereret_utc']}", "",
          "Realistisk: handel til næste dags åbning, alt i DKK, kurtage 0,10 % (min. 29 kr.) + vekselgebyr 0,25 %. "
          "13 startuger pr. model, medianer.", ""]
    for p in PERIODS:
        f0 = res[MODELS[1].name][p]
        md += [f"## {p} — ACWI {f0['acwi']}%/år (DD {f0['acwi_dd']}%, Sharpe {f0['acwi_sh']}) · "
               f"MTUM {f0['mtum']}%/år (DD {f0['mtum_dd']}%, Sharpe {f0['mtum_sh']})", "",
               "| Model | Årligt | Spænd | Max DD | Sharpe | Slår ACWI | Slår MTUM | Handler |",
               "|---|---|---|---|---|---|---|---|"]
        for name, r in res.items():
            x = r[p]
            md.append(f"| {name} | {x['cagr']}% | {x['cagr_p25']}–{x['cagr_p75']}% | {x['dd']}% | {x['sharpe']} | "
                      f"{x['slaar_acwi']}% | {x['slaar_mtum']}% | {x['handler']} |")
        md.append("")
    md += ["## Robusthed (hele perioden)", ""]
    for name, r in robust.items():
        u = r["uden_top5"]
        md += [f"**{name}**", "",
               f"- 30 tilfældige 70 %-universer: årligt {r['bootstrap_cagr_p10']}% (10 %) / "
               f"{r['bootstrap_cagr_median']}% (median) / {r['bootstrap_cagr_p90']}% (90 %), "
               f"slår ACWI i {r['bootstrap_slaar_acwi_pct']}%",
               f"- Top 5 bidragsydere: {', '.join(r['top5_bidrag'])}",
               f"- Uden top 5: årligt {u['cagr']}% (ACWI {u['acwi']}%), Sharpe {u['sharpe']}, slår ACWI i {u['slaar_acwi']}%", ""]
    md.append("Forbehold: universet er valgt i dag (overlevelses-bias); popularitet/fundamental/Aktieguld kan ikke "
              "testes historisk. Research, ikke rådgivning.")
    with open("output/validate_models.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
