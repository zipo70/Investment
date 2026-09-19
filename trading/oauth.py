"""
trading/oauth.py — Lag 3: OAuth 2.0 mod Saxo Bank OpenAPI (SIM).

To dele:
  1. Første-gangs autorisation (authorization-code flow) — kræver ÉT
     manuelt skridt: log ind i en browser og godkend appen. Kør denne
     fra din EGEN maskine (`python -m trading.oauth --authorize`),
     ALDRIG fra en scheduled job (den kan ikke åbne en browser for dig).
     Relevant lige nu, jf. dit svar "har kun oprettet appen, intet token
     endnu".
  2. Løbende refresh — bruges af alt andet i eksekveringslaget via
     get_valid_access_token(). Fejler refresh (fx fordi refresh-tokenet
     er udløbet/tilbagekaldt), STOPPER agenten sig selv
     (guardrails.activate_kill_switch) og sender en notifikation, i
     stedet for at fortsætte i en ukendt/uautoriseret tilstand — præcis
     som krævet i briefen.

Saxo bruger et almindeligt OAuth 2.0 authorization-code-flow. Slå de
PRÆCISE, aktuelle SIM-endpoints op i Saxos developer-portal (de kan ændre
sig) og sæt dem som miljøvariabler frem for at hardkode dem her:
  SAXO_AUTH_URL     (typisk noget i retning af https://sim.logonvalidation.net/authorize)
  SAXO_TOKEN_URL    (typisk noget i retning af https://sim.logonvalidation.net/token)
  SAXO_API_BASE_URL (typisk https://gateway.saxobank.com/sim/openapi)
"""

import http.server
import os
import sys
import time
import urllib.parse

import requests

from trading import token_store
from trading.guardrails import activate_kill_switch
from notify import notify_order_activity

TOKEN_URL = os.environ.get("SAXO_TOKEN_URL", "https://sim.logonvalidation.net/token")
AUTH_URL = os.environ.get("SAXO_AUTH_URL", "https://sim.logonvalidation.net/authorize")
CLIENT_ID = os.environ.get("SAXO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("SAXO_CLIENT_SECRET")  # nogle Saxo-apps er "public" og har ingen
REDIRECT_URI = os.environ.get("SAXO_REDIRECT_URI", "http://localhost:12321/callback")

# Hvor tæt på faktisk udløb (sekunder) vi vælger at refreshe i forvejen.
REFRESH_MARGIN_SECONDS = 60


class OAuthError(Exception):
    pass


def _exchange(payload: dict) -> dict:
    resp = requests.post(TOKEN_URL, data=payload, timeout=20)
    if resp.status_code >= 300:
        raise OAuthError(f"Token-endpoint fejlede (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    data["obtained_at"] = time.time()
    return data


def run_first_time_authorization():
    """Kør ÉN GANG, manuelt, fra din egen maskine. Åbner en browser til
    Saxos login-side, fanger redirect'et lokalt med en midlertidig
    HTTP-server, og bytter authorization-code'en til det første
    access+refresh-token-par, som gemmes via token_store.save_tokens()."""
    if not CLIENT_ID:
        raise OAuthError("SAXO_CLIENT_ID mangler (sæt som miljøvariabel).")

    state = "mineaktier-" + str(int(time.time()))
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Åbner browser til Saxo-login. Log ind og godkend appen...")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    print(f"(Hvis browseren ikke åbner selv, gå manuelt til denne adresse:)\n{url}\n")

    auth_result = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            parsed = urllib.parse.parse_qs(qs)
            auth_result["code"] = parsed.get("code", [None])[0]
            auth_result["state"] = parsed.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"OK - du kan lukke dette vindue og gaa tilbage til terminalen."
            )

        def log_message(self, *args):
            pass  # ingen støj i terminalen fra HTTP-serverens standardlog

    port = int(urllib.parse.urlparse(REDIRECT_URI).port or 12321)
    server = http.server.HTTPServer(("localhost", port), _Handler)
    print(f"Venter på redirect på {REDIRECT_URI} ...")
    while "code" not in auth_result:
        server.handle_request()

    if auth_result.get("state") != state:
        raise OAuthError("State matcher ikke det forventede — muligt CSRF-forsøg, afbryder.")
    if not auth_result.get("code"):
        raise OAuthError("Intet 'code'-parameter i redirect — login mislykkedes eller blev afvist.")

    tokens = exchange_code_for_tokens(auth_result["code"])
    print("Første token hentet og gemt (se token_store.py for hvilken backend).")
    print("Kør nu eksekveringsjobbet normalt (trading/run_execution_cycle.py).")
    return tokens


def build_authorization_url(state: str = None) -> tuple:
    """Bygger autorisations-URL'en UDEN at åbne en browser eller starte en
    lokal server — til brug når du ikke kan/vil have en Python-terminal
    på selve telefonen/enheden, der skal logge ind (se
    SAXO_INTEGRATION.md, 'Hosting via GitHub Actions'). Du besøger selv
    URL'en manuelt (i Safari/Chrome), logger ind, og kopierer bagefter
    'code'-parameteret fra adresselinjen — bemærk at browseren typisk
    viser en fejl ('kan ikke åbne siden') ved selve redirect'et til
    localhost, men adresselinjen beholder alligevel den fulde URL med
    koden i, som du kan kopiere derfra."""
    if not CLIENT_ID:
        raise OAuthError("SAXO_CLIENT_ID mangler (sæt som miljøvariabel).")
    state = state or ("mineaktier-" + str(int(time.time())))
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}", state


def exchange_code_for_tokens(code: str) -> dict:
    """Bytter en (manuelt indhentet) authorization-code til det første
    access+refresh-token-par og gemmer det via token_store (backend
    styret af SAXO_TOKEN_BACKEND — se token_store.py). Bruges både af
    run_first_time_authorization() (browser+lokal-server-flowet) og af
    .github/workflows/saxo_bootstrap_authorize.yml's
    'python -m trading.oauth --exchange-code'-kald (det telefon-/
    GitHub-only-venlige flow, hvor koden kopieres manuelt fra
    adresselinjen — se build_authorization_url())."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
    }
    if CLIENT_SECRET:
        payload["client_secret"] = CLIENT_SECRET
    tokens = _exchange(payload)
    token_store.save_tokens(tokens)
    return tokens


def refresh_access_token(tokens: dict) -> dict:
    """Bytter det gemte refresh-token til et nyt access(+refresh)-token.
    Saxo roterer typisk selve refresh-tokenet ved brug — det NYE SKAL
    altid gemmes igen, ellers dør kæden ved næste kørsel."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": CLIENT_ID,
    }
    if CLIENT_SECRET:
        payload["client_secret"] = CLIENT_SECRET
    new_tokens = _exchange(payload)
    token_store.save_tokens(new_tokens)
    return new_tokens


def get_valid_access_token() -> str:
    """Hovedindgang for resten af eksekveringslaget: returnerer et
    garanteret gyldigt access-token, og refresher stille og roligt hvis
    det er ved at udløbe.

    FEJLER refresh (eller findes der slet intet token endnu), stopper
    denne funktion agenten selv (kill switch) og sender en notifikation,
    fremfor at fortsætte med et token der måske allerede er dødt — jf.
    briefens eksplicitte krav."""
    tokens = token_store.load_tokens()
    if tokens is None:
        activate_kill_switch(
            "Intet gemt OAuth-token fundet — kør "
            "'python -m trading.oauth --authorize' manuelt først."
        )
        notify_order_activity(
            "Eksekvering stoppet — intet token",
            "Intet OAuth-token fundet. Kør første-gangs-autorisationen manuelt "
            "fra din egen maskine, fjern derefter trading/state/KILL_SWITCH.",
        )
        raise OAuthError("Intet token — første-gangs-autorisation mangler.")

    age = time.time() - tokens.get("obtained_at", 0)
    expires_in = tokens.get("expires_in", 1200)
    if age < expires_in - REFRESH_MARGIN_SECONDS:
        return tokens["access_token"]

    try:
        tokens = refresh_access_token(tokens)
        return tokens["access_token"]
    except Exception as e:
        activate_kill_switch(f"OAuth-refresh fejlede: {e}")
        notify_order_activity(
            "Eksekvering stoppet — OAuth-fejl",
            f"Kunne ikke forny Saxo-tokenet, agenten er stoppet automatisk "
            f"(kill switch aktiveret).\nFejl: {e}\n\n"
            "Kør 'python -m trading.oauth --authorize' igen for at "
            "genautorisere, fjern derefter trading/state/KILL_SWITCH manuelt.",
        )
        raise


if __name__ == "__main__":
    if "--authorize" in sys.argv:
        # Kræver en rigtig computer med browser — se docstring øverst.
        run_first_time_authorization()
    elif "--print-auth-url" in sys.argv:
        # Telefon-/GitHub-only-flowet: ingen browser/server startes her.
        url, state = build_authorization_url()
        print(url)
        print(f"\n(state brugt: {state} — ikke strengt nødvendigt at bekræfte "
              "i det manuelle flow, men kopiér HELE adresselinjen efter "
              "redirect'et, ikke kun 'code'-værdien, hvis du er i tvivl.)")
    elif "--exchange-code" in sys.argv:
        idx = sys.argv.index("--exchange-code")
        if idx + 1 >= len(sys.argv):
            print("Brug: python -m trading.oauth --exchange-code <code>")
            sys.exit(1)
        code = sys.argv[idx + 1]
        exchange_code_for_tokens(code)
        print("Token hentet og gemt via den konfigurerede token_store-backend.")
    else:
        print(
            "Brug:\n"
            "  python -m trading.oauth --authorize        (kræver browser, kør lokalt)\n"
            "  python -m trading.oauth --print-auth-url   (ingen browser nødvendig her)\n"
            "  python -m trading.oauth --exchange-code X  (bruges af bootstrap-workflowet)"
        )
