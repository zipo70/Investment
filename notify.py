"""
notify.py — Fælles notifikations-hjælper (ntfy.sh eller Twilio-SMS).

Udtrukket fra alert_check.py 2026-09-19, i forbindelse med Saxo Bank-
integrationen (se trading/-mappen og SAXO_INTEGRATION.md), så både det
daglige køb/sælg-tjek OG den nye eksekveringsservice kan bruge samme
funktion og samme GitHub-secrets, uden kode-duplikering.

Opsætning: se README.md, "Automatisk dagligt tjek".
"""

import os
import sys

import requests


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


def notify_order_activity(title: str, body: str) -> bool:
    """Bruges specifikt af trading/-modulerne (guardrails, order_executor,
    oauth) for at opfylde briefens krav om 'notifikation ved AL
    ordreaktivitet' — dvs. også ved afviste ordrer, dry-run-simuleringer
    og fejl, ikke kun ved faktisk gennemførte handler. Sender via samme
    kanal som send_notification() (ntfy/Twilio); holdes som sit eget navn
    så det i koden er tydeligt HVOR det specifikke krav fra Saxo-briefen
    bliver opfyldt."""
    return send_notification(f"[Saxo] {title}", body)
