# Modelvalidering — 2026-10-25T14:14:35+00:00

Realistisk: handel til næste dags åbning, alt i DKK, kurtage 0,10 % (min. 29 kr.) + vekselgebyr 0,25 %. 13 startuger pr. model, medianer.

## Træning 2015–2022 — ACWI 7.5%/år (DD -31.7%, Sharpe 0.52) · MTUM 11.9%/år (DD -28.2%, Sharpe 0.7)

| Model | Årligt | Spænd | Max DD | Sharpe | Slår ACWI | Slår MTUM | Handler |
|---|---|---|---|---|---|---|---|
| A0. Original som før (samme dag, lokal valuta) | 26.6% | 25.0–30.2% | -36.5% | 1.0 | 100% | 100% | 100 |
| A. Original realistisk | 24.9% | 20.8–28.1% | -37.4% | 0.94 | 100% | 100% | 100 |
| B. Original + sektorloft (maks 2) | 24.9% | 17.8–25.6% | -37.0% | 0.92 | 100% | 100% | 104 |
| C. 10 aktier + sektorloft (maks 3) | 18.6% | 14.9–20.0% | -33.1% | 0.86 | 100% | 100% | 192 |
| D. 12-1 momentum + sektorloft | 19.9% | 17.9–24.4% | -32.0% | 0.81 | 100% | 92% | 81 |
| E. App-model, 20 dages hold | 3.3% | 3.2–3.3% | -37.9% | 0.27 | 0% | 0% | 505 |
| F. App-model, kvartalsvis | 10.2% | 5.4–12.9% | -24.5% | 0.69 | 69% | 31% | 132 |
| G. Momentum + app-filter + sektorloft | 15.3% | 15.0–20.1% | -35.8% | 0.7 | 100% | 85% | 107 |

## Test 2023→ — ACWI 17.5%/år (DD -19.1%, Sharpe 1.29) · MTUM 21.8%/år (DD -23.5%, Sharpe 1.03)

| Model | Årligt | Spænd | Max DD | Sharpe | Slår ACWI | Slår MTUM | Handler |
|---|---|---|---|---|---|---|---|
| A0. Original som før (samme dag, lokal valuta) | 24.9% | 22.3–31.7% | -25.8% | 0.99 | 77% | 69% | 51 |
| A. Original realistisk | 23.6% | 17.3–29.4% | -27.4% | 0.89 | 69% | 62% | 51 |
| B. Original + sektorloft (maks 2) | 19.5% | 14.3–24.5% | -28.7% | 0.85 | 54% | 31% | 52 |
| C. 10 aktier + sektorloft (maks 3) | 18.7% | 15.3–19.4% | -25.5% | 0.9 | 62% | 0% | 97 |
| D. 12-1 momentum + sektorloft | 21.8% | 16.0–24.9% | -30.4% | 0.87 | 69% | 54% | 40 |
| E. App-model, 20 dages hold | 10.4% | 9.2–11.8% | -16.4% | 0.73 | 0% | 0% | 235 |
| F. App-model, kvartalsvis | 8.2% | 4.5–11.9% | -18.6% | 0.58 | 8% | 0% | 64 |
| G. Momentum + app-filter + sektorloft | 13.6% | 11.6–23.0% | -27.9% | 0.65 | 46% | 23% | 54 |

## Hele perioden — ACWI 10.8%/år (DD -31.7%, Sharpe 0.74) · MTUM 14.4%/år (DD -28.2%, Sharpe 0.79)

| Model | Årligt | Spænd | Max DD | Sharpe | Slår ACWI | Slår MTUM | Handler |
|---|---|---|---|---|---|---|---|
| A0. Original som før (samme dag, lokal valuta) | 24.7% | 22.8–28.5% | -36.8% | 0.96 | 100% | 100% | 155 |
| A. Original realistisk | 23.0% | 20.0–26.2% | -38.0% | 0.91 | 100% | 100% | 155 |
| B. Original + sektorloft (maks 2) | 20.9% | 18.4–23.2% | -37.0% | 0.82 | 100% | 100% | 156 |
| C. 10 aktier + sektorloft (maks 3) | 17.4% | 15.8–18.5% | -33.1% | 0.83 | 100% | 92% | 295 |
| D. 12-1 momentum + sektorloft | 19.6% | 15.9–23.4% | -32.9% | 0.81 | 100% | 85% | 126 |
| E. App-model, 20 dages hold | 5.1% | 5.1–5.2% | -37.9% | 0.38 | 0% | 0% | 751 |
| F. App-model, kvartalsvis | 8.8% | 4.6–11.4% | -25.0% | 0.63 | 31% | 15% | 199 |
| G. Momentum + app-filter + sektorloft | 16.1% | 13.5–17.6% | -36.6% | 0.73 | 100% | 62% | 166 |

## Robusthed (hele perioden)

**A. Original realistisk**

- 30 tilfældige 70 %-universer: årligt 12.5% (10 %) / 21.8% (median) / 26.5% (90 %), slår ACWI i 90%
- Top 5 bidragsydere: NVDA, TSLA, TSM, GOOGL, NFLX
- Uden top 5: årligt 12.6% (ACWI 10.8%), Sharpe 0.64, slår ACWI i 77%

**B. Original + sektorloft (maks 2)**

- 30 tilfældige 70 %-universer: årligt 13.2% (10 %) / 20.0% (median) / 24.4% (90 %), slår ACWI i 97%
- Top 5 bidragsydere: NVDA, TSLA, GOOGL, NFLX, INFY
- Uden top 5: årligt 12.5% (ACWI 10.8%), Sharpe 0.65, slår ACWI i 62%

Forbehold: universet er valgt i dag (overlevelses-bias); popularitet/fundamental/Aktieguld kan ikke testes historisk. Research, ikke rådgivning.
