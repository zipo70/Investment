"""
alert_check.py — automatisk dagligt køb/sælg-tjek for "Mine Aktier".

Køres IKKE manuelt i normal brug — den er lavet til at blive kørt af GitHub
Actions-workflowet `.github/workflows/daily_alert.yml`, som kører
uafhængigt af om appen er åben på telefonen (helt uafhængigt af Streamlit).

Hvad den gør:
1. Læser tickerne fra `watchlist_tickers.txt` (redigér selv denne fil på
   GitHub for at tilføje/fjerne aktier fra det automatiske tjek — den er
   adskilt fra app'ens egen watchlist, som er midlertidig og nulstilles
   ved app-genstart, se README.md).
2. Kører Top 10-scanningen (samme logik som Top 10-fanen i appen).
3. Analyserer alle tickere fra begge lister (uden dubletter) med samme
   KØB/HOLD/SÆLG-logik som resten af værktøjet.
4. Sender én samlet notifikation, hvis mindst én ticker viser et KØB- eller
   SÆLG-signal. Ingen notifikation på stille dage (alt HOLD) — for ikke at
   spamme med daglige "intet nyt"-beskeder.

Notifikationsmetode (se README.md, "Automatisk dagligt tjek" for opsætning):
- Gratis, anbefalet (ntfy.sh, ingen konto): sæt GitHub-secret'en
  NTFY_TOPIC til et langt, hemmeligt kanalnavn, og abonnér på samme kanal
  i ntfy-appen på telefonen. 100% gratis, ingen løbende omkostning.
- Rigtig SMS (valgfrit, koster nogle øre pr. besked — Twilio har ingen
  gratis SMS-service, der virker pålideligt til danske numre): sæt
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER og
  TWILIO_TO_NUMBER som secrets — bruges i stedet for ntfy hvis alle fire
  er sat.
- Er intet af ovenstående sat op, printes resultatet blot til kørslens log
  (synligt under "Actions" på GitHub) uden fejl.
"""

import datetime

import model_a
from notify import send_notification  # udtrukket 2026-09-19, se notify.py

# MODEL A (2026-10-25): notifikation KUN på rebalanceringsdage med den nye
# top 5 (køb) — øvrige dage er der intet at gøre (stop-loss ligger i ordren).
# Watchlist-aktier der er faldet under 200-dages snit nævnes som info samme dag.


def main():
    rd = model_a.ranking(force=True)
    reb = rd["rebalance"]
    print(f"Model A top {model_a.N_POSITIONS}: {rd['top']} · rebalanceringsdag: {reb['is_rebalance_day']} "
          f"· næste: {reb['next']}")
    if not reb["is_rebalance_day"]:
        print("Ikke rebalanceringsdag — ingen notifikation sendt.")
        return

    lines = [f"Model A rebalancering {datetime.date.today().isoformat()}:"]
    for t in rd["top"]:
        r = rd["by_ticker"][t]
        lines.append(f"KØB/BEHOLD {t} @ {r['last']:.2f} (momentum {r['momentum'] * 100:+.0f}%, "
                     f"stop {r['last'] * (1 - model_a.STOP_PCT):.2f})")
    lines.append("SÆLG alle øvrige positioner fra modellen.")
    send_notification(f"Mine Aktier: model A rebalancering", "\n".join(lines) + "\n\nIkke finansiel rådgivning.")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
