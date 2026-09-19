"""
trading/token_store.py — Gemmer OAuth-tokens SEPARAT fra trading.db.

trading.db forventes committet til git i den simple GitHub Actions-
opsætning (se db.py og SAXO_INTEGRATION.md) — tokens skal ALDRIG committes
dertil, uanset om repoet er privat eller offentligt. Derfor sit eget,
lille modul med to backends, valgt via miljøvariablen SAXO_TOKEN_BACKEND:

  - "file" (default): en lokal, gitignoret JSON-fil. Den KORREKTE og
    enkle løsning hvis eksekveringslaget kører fra din egen maskine eller
    en VPS med vedvarende disk.
  - "github_secret": tokens gemmes/hentes som et krypteret GitHub
    Actions-secret i stedet for en fil. Løser persistens-problemet i
    GitHub Actions (som ikke har vedvarende disk mellem kørsler) UDEN en
    VPS — se SAXO_INTEGRATION.md, "Hosting via GitHub Actions (uden
    telefon-terminal/VPS)" for den fulde opsætning (kræver et
    Personal Access Token, gemt som sit eget secret GH_PAT_FOR_SECRETS).
    Bruges af .github/workflows/trading_execution.yml og
    .github/workflows/saxo_bootstrap_authorize.yml.
"""

import base64
import json
import os
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).resolve().parent / "state" / "saxo_tokens.json"


def _backend() -> str:
    return os.environ.get("SAXO_TOKEN_BACKEND", "file").strip().lower()


def save_tokens(tokens: dict):
    backend = _backend()
    if backend == "github_secret":
        GitHubSecretTokenStore.from_env().save_tokens(tokens)
    else:
        _save_tokens_local(tokens)


def load_tokens():
    backend = _backend()
    if backend == "github_secret":
        return GitHubSecretTokenStore.from_env().load_tokens()
    return _load_tokens_local()


# --- Backend 1: lokal fil ------------------------------------------------

def _save_tokens_local(tokens: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass  # bedst-mulig på platforme uden POSIX-filrettigheder


def _load_tokens_local():
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text())


# --- Backend 2: krypteret GitHub Actions-secret --------------------------

class TokenStoreError(Exception):
    pass


class GitHubSecretTokenStore:
    """Gemmer/henter tokens som ét krypteret repo-secret.

    LÆSNING sker IKKE via GitHub's API (secrets kan ikke læses tilbage
    som klartekst der) — den sker via miljøvariablen som GitHub selv
    injicerer i workflow-kørslen, når secret'et er nævnt i workflowets
    `env:`-blok (`${{ secrets.SAXO_TOKENS_JSON }}`). Denne klasse læser
    derfor blot `os.environ[secret_name]`.

    SKRIVNING (gemme et NYT/rotere et token) sker via GitHub's REST API:
    kryptér JSON-værdien mod repoets public key (libsodium "sealed box",
    som GitHub selv kræver) og PUT den til
    /repos/{repo}/actions/secrets/{secret_name}. Kræver et Personal
    Access Token med skrive-adgang til repoets Actions-secrets, sat som
    sin egen miljøvariabel (secret) — se SAXO_INTEGRATION.md."""

    API_BASE = "https://api.github.com"

    def __init__(self, repo: str = None, pat: str = None, secret_name: str = "SAXO_TOKENS_JSON"):
        # Bevidst IKKE valideret her: load_tokens() har brug for hverken
        # repo eller pat (den læser blot en miljøvariabel) — kun
        # save_tokens() (som rent faktisk kalder GitHub's API) validerer,
        # med en fejlbesked der peger på hvad der mangler.
        self.repo = repo
        self.pat = pat
        self.secret_name = secret_name

    @classmethod
    def from_env(cls) -> "GitHubSecretTokenStore":
        return cls(
            repo=os.environ.get("GITHUB_REPOSITORY") or os.environ.get("SAXO_GH_REPO"),
            pat=os.environ.get("GH_PAT_FOR_SECRETS"),
            secret_name=os.environ.get("SAXO_TOKENS_SECRET_NAME", "SAXO_TOKENS_JSON"),
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def load_tokens(self):
        """Læser fra miljøvariablen (injiceret af GitHub Actions' `env:`),
        IKKE via API'et — se klasse-docstring."""
        raw = os.environ.get(self.secret_name)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise TokenStoreError(
                f"Kunne ikke parse indholdet af secret'et {self.secret_name!r} som JSON: {e}"
            )

    def save_tokens(self, tokens: dict):
        if not self.repo:
            raise TokenStoreError(
                "Intet repo angivet (GITHUB_REPOSITORY mangler — sæt SAXO_GH_REPO "
                "manuelt hvis du kører uden for en GitHub Actions-kørsel)."
            )
        if not self.pat:
            raise TokenStoreError(
                "GH_PAT_FOR_SECRETS mangler — nødvendig for at kunne opdatere "
                "GitHub-secret'et med et nyt/roteret token. Se SAXO_INTEGRATION.md."
            )
        try:
            from nacl import encoding, public
        except ImportError as e:
            raise TokenStoreError(
                "PyNaCl mangler (pip install pynacl) — nødvendig for at kryptere "
                "secret-værdien mod repoets public key, jf. GitHub's krav."
            ) from e

        key_resp = requests.get(
            f"{self.API_BASE}/repos/{self.repo}/actions/secrets/public-key",
            headers=self._headers(), timeout=20,
        )
        if key_resp.status_code >= 300:
            raise TokenStoreError(
                f"Kunne ikke hente repoets public key (HTTP {key_resp.status_code}): {key_resp.text}"
            )
        key_data = key_resp.json()
        public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)

        plaintext = json.dumps(tokens).encode("utf-8")
        encrypted = sealed_box.encrypt(plaintext)
        encrypted_b64 = base64.b64encode(encrypted).decode("utf-8")

        put_resp = requests.put(
            f"{self.API_BASE}/repos/{self.repo}/actions/secrets/{self.secret_name}",
            headers=self._headers(),
            json={"encrypted_value": encrypted_b64, "key_id": key_data["key_id"]},
            timeout=20,
        )
        if put_resp.status_code not in (201, 204):
            raise TokenStoreError(
                f"Kunne ikke opdatere secret'et {self.secret_name!r} "
                f"(HTTP {put_resp.status_code}): {put_resp.text}"
            )
