# Saxo Bank OpenAPI — automatisk ordreeksekvering (KUN SIM)

Status: **skelet, ikke produktionsklar**. Skrevet 2026-09-19 efter din
brief om at udvide "Mine Aktier" med automatisk ordreafgivelse mod Saxo
Banks SIM-miljø (`gateway.saxobank.com/sim/openapi`). Ingen live-handel —
det er bevidst ikke bygget ind, og adskillige steder nedenfor er markeret
`TODO` hvor du selv skal udfylde detaljer, du kan bekræfte/teste mod din
egen Saxo-konto (som jeg ikke har adgang til).

**Læs dette dokument, før du sætter noget op** — særligt afsnittet
"Hosting", som handler om en reel begrænsning (ikke bare et valg af
bekvemmelighed).

## Arkitektur — tre lag, der ikke kalder hinanden direkte

```
Lag 1: Signal          Lag 2: Beslutning        Lag 3: Eksekvering
signal_job.py     -->   trading/decision.py  -->  trading/run_execution_cycle.py
(genbruger core.py)     (REN funktion,            (OAuth, Uic-opslag,
                         ingen netværk)            ordreafgivelse, overvågning)
        |                                                  |
        \--------------------> trading/state/trading.db <--/
                    (SQLite — se "Hvorfor SQLite" nedenfor)
```

- **Lag 1 (Signal)**: `signal_job.py`, et nyt scheduled job der kører
  akkurat som `alert_check.py` (samme `core.analyze_ticker`), men i
  stedet for at sende en tekst-notifikation skriver den strukturerede
  signaler til `trading/state/trading.db`.
- **Lag 2 (Beslutning)**: `trading/decision.py`. En **ren funktion**
  (`decide(signal, portfolio, cfg)`) uden netværk, filadgang eller
  SQLite — anvender de samme porteføljeregler som backtesten (maks 5
  positioner, ligevægtet 20% hver, jf. dit svar "Samme som backtesten").
  100% unit-testbar, se `tests/test_decision.py` (9 tests, kører uden
  netværk — kør `python tests/test_decision.py`).
- **Lag 3 (Eksekvering)**: `trading/`-pakken i øvrigt — OAuth mod Saxo,
  Yahoo→Saxo instrument-mapping, selve ordreafgivelsen (entry + stop i
  ét kald), overvågning af ordrer/positioner, og alle sikkerhedsspærrer.

Lagene "taler" kun sammen via SQLite-databasen — aldrig via direkte
funktionskald mellem fx `signal_job.py` og `trading/order_executor.py`.

## Hvorfor SQLite som overleveringsmekanisme (din åbne opgave til mig)

Du bad mig selv vælge/foreslå fil, SQLite eller kø, hvis jeg havde en
præference — jeg valgte **SQLite**, begrundet i:

1. **Idempotens**: hvert signal får en unik, deterministisk
   `external_reference` (`trading/decision.make_external_reference` —
   samme (ticker, retning, dag) giver altid samme reference). En
   UNIQUE-constraint i databasen forhindrer et genstartet/genkørt job i
   at indsende samme ordre to gange, uden at man selv skal bygge den
   logik i en separat kø-broker.
2. **Revisionsspor**: så snart der er tale om ordrer mod en (om end kun
   SIM-) mæglerkonto, er det værd at kunne se hele historikken —
   signal → beslutning → hvad der rent faktisk blev sendt til Saxo, og
   hvad Saxo svarede.
3. **Minimalt driftsoverhead**: én fil, ingen separat broker-proces
   (Redis/RabbitMQ) at holde kørende ved siden af GitHub Actions.

Se `trading/db.py` for det fulde skema (signals, orders, positions,
instrument_cache, order_activity_log).

## Hosting — den ubehagelige, men vigtige del

Du svarede "ved ikke endnu / skal findes" til hvor Lag 3 skal køre, og
har efterfølgende kun en telefon til rådighed (ingen pc/Mac, ingen
terminal-app). Her er den ærlige afvejning, og løsningen der faktisk
passer til det:

**Det egentlige problem er ikke beregning — det er PERSISTENS.** Lag 3
skal huske et OAuth refresh-token mellem kørsler. GitHub Actions har
**ingen** vedvarende disk mellem kørsler, og den normale
autorisationsproces (`--authorize`) kræver en browser OG en lokal
server på SAMME enhed — noget en telefon uden terminal-app ikke kan
levere. Løsningen nedenfor undgår begge dele.

### Anbefalet, telefon-/GitHub-only opsætning (implementeret, ikke kun skitseret)

`trading/token_store.py` har nu to lagre, valgt via miljøvariablen
`SAXO_TOKEN_BACKEND`:
- `file` (default) — den lokale JSON-fil, til brug hvis du en dag får
  adgang til en pc/VPS.
- `github_secret` — tokens gemmes/hentes som et krypteret GitHub
  Actions-secret (`SAXO_TOKENS_JSON`). **Læsning** sker gratis via
  workflowets egen `env:`-blok (secrets injiceres automatisk som
  miljøvariabler i selve kørslen). **Skrivning** (nyt/roteret token)
  sker via GitHub's API med `PyNaCl`-kryptering mod repoets public key
  — det er sådan GitHub selv kræver at secrets opdateres udefra.

**Engangs-opsætning, alt sammen via Safari på telefonen:**

1. **Opret et Personal Access Token** til at opdatere secrets med: gå
   til GitHub → din profil → Settings → Developer settings → Personal
   access tokens → Fine-grained tokens → generér ét scoped til KUN
   `zipo70/Investment`, med rettigheden **"Secrets" → Read and write**.
   Gem selve token-strengen som et nyt repo-secret ved navn
   `GH_PAT_FOR_SECRETS` (Settings → Secrets and variables → Actions →
   New repository secret) — ja, det er et secret der bruges til at
   opdatere andre secrets, det er en kendt, sikker mønster.
2. **Tilføj `SAXO_CLIENT_ID` og `SAXO_CLIENT_SECRET`** som repo-secrets
   (App Key/App Secret fra din nye Saxo-app).
3. **Byg autorisations-URL'en manuelt** — indsæt dit App Key i denne
   skabelon og åbn den i Safari:
   ```
   https://sim.logonvalidation.net/authorize?response_type=code&client_id=DIT_APP_KEY&redirect_uri=http://localhost:12321/callback&state=mineaktier
   ```
   Log ind med din SIM-konto og godkend appen. Safari vil bagefter vise
   en fejl ("kan ikke åbne siden") ved selve redirect'et til
   `localhost` — det er FORVENTET, ingen server lytter der. Adresselinjen
   beholder alligevel den fulde URL, inklusive `?code=...` — kopiér HELE
   den værdi (uden `code=`-præfikset).
4. **Kør bootstrap-workflowet**: GitHub → dit repo → Actions →
   "Saxo — første-gangs autorisation" → **Run workflow** → indsæt koden
   fra trin 3 i feltet → Run. Den bytter koden til et token og gemmer det
   krypteret som `SAXO_TOKENS_JSON` — helt uden at token'et nogensinde
   rammer git-historikken eller vores samtale.
5. **Kør `trading_execution.yml` manuelt** ("Run workflow") for at teste
   i dry-run. Den er sat op til `SAXO_TOKEN_BACKEND=github_secret`, så
   den både læser og (ved refresh) opdaterer samme secret automatisk
   fremover.

Kun når du er tilfreds med testkørslerne: slå `schedule`-linjen til i
`trading_execution.yml` for automatisk kørsel — den er bevidst
udkommenteret indtil da.

**Begrænsning ved denne opsætning:** kun polling (REST, se
`trading/monitor.poll_once`), ikke en ægte vedvarende WebSocket-
forbindelse (`run_websocket_forever` kræver en proces der aldrig
stopper, hvilket GitHub Actions ikke er bygget til). Det betyder et par
minutters forsinkelse i statusopdateringer, ikke øjeblikkelig besked —
fuldt tilstrækkeligt til denne brug.

### Alternativ, hvis du en dag får en pc/VPS til rådighed

`trading/token_store.py`'s fil-backend virker uændret der (`SAXO_TOKEN_BACKEND=file`,
default), og det er FØRST der at ægte WebSocket-overvågning reelt giver
mening, fordi processen rent faktisk bliver ved med at køre. Ikke
nødvendigt lige nu — kun relevant hvis GitHub-only-opsætningen ovenfor
en dag føles utilstrækkelig.

`signal_job.py` (Lag 1) kører i øvrigt fint og sikkert på GitHub Actions
allerede nu (`.github/workflows/signal_job.yml`), uafhængigt af alt
ovenstående, da den ikke skal huske noget følsomt mellem kørsler.

## Guardrails — allerede implementeret og testet

Se `trading/guardrails.py`. Alle håndhæves i `order_executor.execute_order()`
FØR noget sendes til Saxo:

- `SAXO_DRY_RUN` — default `true`. Skal sættes EKSPLICIT til `false` for
  at sende rigtige SIM-ordrer.
- `SAXO_MAX_ORDER_AMOUNT` — loft pr. ordre (kontoens valuta). **Vigtigt:**
  default i koden er 5.000, men med 5 positioner og 100.000 i
  `starting_capital` bliver hver ligevægtet position ~20.000 — sæt selv
  `SAXO_MAX_ORDER_AMOUNT` til noget der reelt matcher din kontostørrelse
  (workflowet foreslår 25.000 som eksempel), ellers afviser guardrailen
  hver eneste ordre. Dette blev faktisk fanget under test af skelettet.
- `SAXO_MAX_ORDERS_PER_DAY` — default 5.
- Kill switch — en fil (`trading/state/KILL_SWITCH`) eller
  `SAXO_KILL_SWITCH=1`. Aktiveres AUTOMATISK af agenten selv hvis
  OAuth-refresh fejler for godt (se `trading/oauth.py`), og skal fjernes
  manuelt for at genoptage — ingen automatisk gen-aktivering.
- Notifikation ved AL ordreaktivitet — `notify.notify_order_activity()`,
  kaldt for hver eneste beslutning i `order_executor.py`: afvist af
  guardrail, dry-run-simulering, precheck-fejl, faktisk sendt ordre, ELLER
  fejl. Ikke kun ved faktisk gennemførte handler.

Alt dette er testet uden netværk (se kørslerne jeg lavede under
udviklingen — guardrail-afvisning, dagslag, kill switch, og en fuld
dry-run-eksekvering med idempotens-tjek virkede alle korrekt).

## Hvad der IKKE er færdigt endnu (udfyld/test selv)

- **`trading/order_executor._build_order_body()`**: skitse af Saxo-
  ordre-JSON'en, ikke verificeret mod den faktiske API-kontrakt. Kør
  altid mod `/trade/v2/orders/precheck` og sammenlign med Saxos egen
  Swagger/dokumentation, før `SAXO_DRY_RUN=false`. Særligt: `Amount`
  skal formentlig være ANTAL AKTIER, ikke et beløb — der mangler en
  omregning via seneste kurs.
- **`trading/instrument_mapping.py`**: søgningen er en simpel
  tekst-baseret Saxo-instrumentsøgning. Er der mere end ét træf, fejler
  den bevidst (i stedet for at gætte) — du skal formentlig selv bygge en
  lille manuel oversættelsestabel for dine mest brugte tickere.
- **`trading/monitor.py`**: `poll_once()` er skrevet, men positions-
  synkroniseringen bruger et placeholder-felt (`_YahooTickerHint`, IKKE
  et rigtigt Saxo-felt) — du skal koble den til en rigtig Uic→Yahoo-
  baglæns-opslagstabel. `run_websocket_forever()` er kun skitseret (se
  "Hosting" ovenfor for hvorfor).
- **`run_execution_cycle._portfolio_from_positions()`**: bruger
  `cfg.starting_capital` som et EKSPLICIT forkert stedfortræder for den
  faktiske kontoværdi — skal kobles til Saxos `/port/v1/balances/me`,
  før `SAXO_DRY_RUN=false`.
- **Første-gangs OAuth-autorisation**: løst for telefon-/GitHub-only
  brug via `--print-auth-url`/`--exchange-code` + bootstrap-workflowet,
  se "Hosting" ovenfor. Har du i stedet en pc/Mac til rådighed en dag,
  kan du bruge den simplere `python -m trading.oauth --authorize`
  (åbner selv en browser og fanger redirect'et).
- **`trading/token_store.GitHubSecretTokenStore`**: implementeret og
  testet (krypterings-round-trip verificeret med PyNaCl) — IKKE længere
  kun en skitse. Selve GitHub API-kaldet er ikke afprøvet mod en rigtig
  konto herfra (jeg har ikke adgang til dit repo), så følg med i
  bootstrap-workflowets log første gang, og sig til hvis noget fejler.

## Ét sidste, vigtigt punkt: repo-synlighed

`trading/state/trading.db` forventes committet til git (se
`trading/db.py`) — det er sådan Lag 1/2/3 deler tilstand mellem
workflow-kørsler uden en rigtig server. Denne fil indeholder tickere,
retninger og tidspunkter for dine signaler/ordrer (ikke kontonumre eller
tokens — dem holder `.gitignore` ude, se `trading/token_store.py`). Er
`zipo70/Investment` et offentligt repo, er denne handelsaktivitet
dermed synlig for alle. Overvej at gøre repoet privat, nu hvor det
rører (om end kun simuleret) rigtig ordreflow — det påvirker ikke
Streamlit-deployet, som Streamlit Community Cloud også understøtter fra
private repos.

## Filoversigt (denne opgave)

| Fil | Lag | Ansvar |
|---|---|---|
| `signal_job.py` | 1 | Nyt scheduled job — skriver signaler til trading.db |
| `trading/decision.py` | 2 | REN beslutningsfunktion — portefølje-regler |
| `trading/db.py` | 1+2+3 | SQLite-skema og hjælpefunktioner (delt tilstand) |
| `trading/guardrails.py` | 3 | Dry-run, beløbsloft, dagsloft, kill switch |
| `trading/oauth.py` | 3 | Første-gangs-autorisation + refresh-loop |
| `trading/token_store.py` | 3 | OAuth-token-lager — lokal fil ELLER krypteret GitHub-secret (`SAXO_TOKEN_BACKEND`) |
| `trading/instrument_mapping.py` | 3 | Yahoo-ticker → Saxo Uic/AssetType, cachet |
| `trading/order_executor.py` | 3 | Sender entry+stop i ét kald, idempotent |
| `trading/monitor.py` | 3 | Synkroniserer ordrer/positioner (polling nu, WebSocket senere) |
| `trading/run_execution_cycle.py` | 3 | Hovedindgang — binder det hele sammen |
| `notify.py` | — | Fælles notifikationslogik (udtrukket fra `alert_check.py`) |
| `tests/test_decision.py` | 2 | 9 netværksfrie unit-tests af beslutningslaget |
| `.github/workflows/signal_job.yml` | 1 | Scheduled kørsel af `signal_job.py` |
| `.github/workflows/trading_execution.yml` | 3 | Manuel (`workflow_dispatch`) kørsel af Lag 3 — se "Hosting" |
| `.github/workflows/saxo_bootstrap_authorize.yml` | 3 | Engangs-workflow: bytter en manuelt indhentet code til det første token |

Kun til eget analysebrug — ikke finansiel rådgivning, og ingen live-handel.
