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

import os
import sys

import requests

from core import analyze_ticker, find_top_candidates, load_static_watchlist


def send_notification(title: str, body: str) -> bool:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    to_number = os.environ.get("TWILIO_TO_NUMBER")
    if account_sid and auth_token and from_number and to_number:
        try:
            resp = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                data={"From": from_number, "To": to_number, "Body": f"{title}\n{body}"},
                auth=(account_sid, auth_token),
                timeout=15,
            )
            if resp.status_code >= 300:
                print(f"Twilio-fejl (HTTP {resp.status_code}): {resp.text}", file=sys.stderr)
                return False
            print("Notifikation sendt via Twilio (rigtig SMS).")
            return True
        except Exception as e:
            print(f"Twilio-undtagelse: {e}", file=sys.stderr)
            return False

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            resp = requests.post(
                f"https://ntfy.sh/{topic}",
                data=body.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Priority": "high",
                    "Tags": "chart_with_upwards_trend",
                },
                timeout=15,
            )
            if resp.status_code >= 300:
                print(f"ntfy-fejl (HTTP {resp.status_code}): {resp.text}", file=sys.stderr)
                return False
            print("Notifikation sendt via ntfy.sh (gratis push).")
            return True
        except Exception as e:
            print(f"ntfy-undtagelse: {e}", file=sys.stderr)
            return False

    print("Ingen notifikationsmetode sat op (hverken Twilio- eller NTFY_TOPIC-secrets fundet).")
    print(f"--- {title} ---\n{body}")
    return False


def main():
    tickers = set(load_static_watchlist())
    print(f"Statisk watchlist ({len(tickers)} tickere): {sorted(tickers)}")

    print("Kører Top 10-scanning (kan tage et par minutter)...")
    top_candidates, stats = find_top_candidates(top_n=10)
    print(f"Top 10-scanning færdig: {stats}")
    for c in top_candidates:
        tickers.add(c["ticker"])

    if not tickers:
        print("Ingen tickere at tjekke (tom watchlist_tickers.txt og ingen Top 10-kandidater).")
        return

    signals = []
    for ticker in sorted(tickers):
        try:
            result = analyze_ticker(ticker)
        except Exception as e:
            print(f"Sprang {ticker} over (uventet fejl: {e})")
            continue
        if not result.get("ok"):
            print(f"Sprang {ticker} over ({result.get('reason')})")
            continue

        rec = result.get("recommendation", {})
        action = rec.get("action")
        price = result["ta"]["last_price"]
        score = result["composite_score"]

        if action in ("KØB", "SÆLG"):
            triggers = rec.get("sell_triggers") if action == "SÆLG" else result["ta"]["ta_triggers"]
            trigger_txt = "; ".join(triggers[:2]) if triggers else "—"
            line = f"{action} {ticker} @ {price:.2f} (score {score:.0f}/100) — {trigger_txt}"
            signals.append(line)
            print(line)
        else:
            print(f"HOLD {ticker} @ {price:.2f} (score {score:.0f}/100)")

    if signals:
        title = f"Mine Aktier: {len(signals)} signal(er)"
        body = "\n".join(signals) + "\n\nIkke finansiel rådgivning."
        send_notification(title, body)
    else:
        print("Ingen KØB/SÆLG-signaler i dag — ingen notifikation sendt.")


if __name__ == "__main__":
    main()
