# Swing trading-agent (paper trading, backtest)

Trend/momentum swing-strategi med 3 måneders rebalanceringscyklus. Bygget til
at teste idéen på historiske data, FØR der overvejes noget som helst med
rigtige penge på Nordnet.

**Status: kun backtest på historiske data. Ingen live-handel, ingen
Nordnet-integration, ingen automatisk ordreafgivelse.** Nordnet har ikke en
åben, offentlig handels-API, så det er bevidst ikke en del af dette projekt.

## Hvorfor kører du det selv?

Sandboxen denne agent blev bygget i har kun netadgang til pakke-registre
(pypi/npm/github) — ikke til kursdata-udbydere som Yahoo Finance. Koden er
derfor testet med syntetisk (falsk) data for at verificere at den kører uden
fejl, men ALDRIG kørt på rigtige kurser. Projektet er desuden bevidst holdt
let (kun `yfinance` + `pandas` + `numpy` — ingen `pyarrow`, ingen
`matplotlib`; graferne er håndbyggede SVG'er i ren Python) netop for at gøre
det realistisk at køre på en telefon, ikke kun en pc.

**Jeg har ikke selv kunnet teste dette på iOS** — jeg har ikke adgang til en
iPhone herfra. Nedenstående er mit bedste bud; hvis noget fejler, så send mig
fejlbeskeden, så retter vi til sammen.

## Kør fra iPhone

Der findes ikke et "terminal-look-and-feel" som Termux (Android) til iOS —
Apple tillader det ikke på samme måde. Jeg har undersøgt dette grundigt
(inkl. slået pakkeafhængigheder op direkte på PyPI) og anbefaler nu i denne
rækkefølge:

| Løsning | Pris | Vurdering |
|---|---|---|
| **Google Colab** (i Safari) | Gratis | **Anbefalet førstevalg.** Kører i skyen, ikke på telefonen — så INGEN installationsproblemer overhovedet. numpy/pandas indbygget, `pip install` virker altid (rigtig Linux-maskine bag kulissen). Tilgængelig fra enhver browser; kan lægges som ikon på hjemmeskærmen ("Føj til hjemmeskærm" i Safari). Kræver en Google-konto. |
| **a-Shell** | Gratis | Godt gratis on-device alternativ, hvis du hellere vil have det kørende lokalt/offline. Python 3.13, numpy/pandas/scipy er prebuilt til iOS. `pip` virker KUN til rene Python-pakker — se pin-anbefaling nedenfor. |
| **Pyto** | Betalt (~15 USD, engangskøb) | Kun relevant hvis de to gratis muligheder driller. Bemærk: jeg kan IKKE bekræfte at Pytos bundlede pakker inkluderer `curl_cffi` — betaling løser altså ikke nødvendigvis samme problem som a-Shell har. |

**Om `yfinance`-versionen — vigtigt, nu verificeret konkret:** Jeg har slået
op direkte i yfinance's afhængigheder på PyPI. Alle versioner fra og med
`0.2.58` kræver `curl_cffi` (kompileret Rust/C-pakke — vil sandsynligvis
FEJLE at installere on-device på iOS). Versionerne `0.2.54`–`0.2.56` kræver
derimod KUN rene Python-pakker (ingen `curl_cffi`, ingen `lxml`) — det er
derfor `requirements.txt` pinner `yfinance==0.2.55`. Ulempen ved den ældre
version er at den er lidt mere udsat for Yahoos anti-bot-blokering end
nyeste version — for en kvartalsvis kørsel af ~80 tickere bør det være en
fin afvejning.

**Fremgangsmåde — Google Colab (anbefalet):**

1. Åbn [colab.research.google.com](https://colab.research.google.com) i
   Safari på telefonen og log ind med en Google-konto.
2. Opret en ny notebook, upload `swing_agent`-mappens filer (eller kopiér
   dem ind som celler) — nemmest: upload som en zip og pak ud med
   `!unzip swing_agent.zip` i en celle.
3. Kør i celler:
   ```
   !pip install -r swing_agent/requirements.txt
   %cd swing_agent
   !python data_fetch.py --check
   !python run_backtest.py
   ```
4. Download eller åbn `output/report.html` direkte fra Colabs filpanel
   (tryk-og-hold → download, eller åbn i ny fane).
5. Læg evt. et Safari-ikon til Colab-notebooken på hjemmeskærmen, så det
   føles som en app.

**Fremgangsmåde — a-Shell (gratis, on-device):**

1. Overfør hele `swing_agent`-mappen til telefonen via iCloud Drive/Filer-
   appen (a-Shell kan køre scripts direkte fra Filer).
2. Åbn a-Shell og installér afhængigheder:
   ```
   pip install -r requirements.txt
   ```
3. Kør miljøtjekket FØR du prøver hele universet:
   ```
   python data_fetch.py --check
   ```
   Den henter kun 5 dages data for én aktie (AAPL) og fortæller om
   opsætningen virker.
4. Virker tjekket, kør den fulde backtest:
   ```
   python run_backtest.py
   ```
5. Åbn `output/report.html` — tryk på filen og vælg "Åbn i Safari", eller
   del den til Filer-appen.

**Hvis `pip install` alligevel fejler** (uanset app): send mig den præcise
fejlbesked — den navngiver altid den pakke der driller, og vi finder en
løsning (fx endnu en version-pin, eller at flytte kun datahentningen til
Colab og synke `cache/`-CSV'erne til telefonen bagefter).

## Kom i gang (pc/Mac, til reference)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_backtest.py
```

Første kørsel henter ~80 tickeres historik via yfinance og cacher dem i
`cache/` som CSV-filer (kan tage et par minutter). Efterfølgende kørsler
bruger cachen — brug `python run_backtest.py --force` for at hente frisk
data.

Resultatet er `output/report.html` — åbn den i en browser.

## Hvad strategien gør

1. **Univers** (`universe.py`): ~80 likvide large/mid-cap aktier fordelt på
   US, Europa, Norden og emerging markets. Ret listen til hvis du vil handle
   et andet sæt aktier.
2. **Hver ~63 handelsdage (1 kvartal)** (`strategy.py`, `backtest.py`):
   - Trendfilter: aktien skal handle over sit 200-dages glidende gennemsnit.
   - Momentum-rangering: 6-måneders afkast, seneste måned udeladt
     ("skip-month"-momentum — akademisk standardkonstruktion).
   - De 5 højest rangerede (der består trendfilteret) udgør målporteføljen,
     ligevægtet (20% hver). Findes færre end 5 kandidater, forbliver resten
     kontant — agenten tvinger IKKE aktier ind i porteføljen i en nedtrend.
3. **Hver ~5 handelsdage (ugentligt)**: stop-loss-tjek — en position lukkes
   hvis den er faldet 15% fra indgangskursen. Provenuet forbliver kontant
   til næste rebalancering.
4. **Omkostninger**: 0,10% pr. handel (min. 29 i lokal valuta) trækkes fra —
   juster i `config.py` til Nordnets faktiske kurtagesatser.

Alle parametre (kapital, antal positioner, lookback-vinduer, stop-loss,
omkostninger) justeres i `config.py` uden at røre strategilogikken.

## Kandidat-screener (TA + analytiker + popularitet)

Et supplerende værktøj, adskilt fra backtesten: en rangeret liste over hvilke
aktier i universet der ser "modne" ud til køb LIGE NU, baseret på teknisk
analyse, analytiker-konsensus og popularitet/sentiment. Output er
`output/screener.html` (samme visuelle stil som backtest-rapporten).

**Vigtigt at forstå:** dette er IKKE en backtest og IKKE en forudsigelse —
det er et øjebliksbillede bygget af gennemsigtige, regelbaserede signaler.
Analytiker-kursmål og social sentiment tager jævnligt fejl. Brug det som
input til egen research, ikke som en købsanbefaling.

**Kør:**
```
python run_screener.py
```
eller i Colab, i en ny celle:
```python
!python run_screener.py
```
```python
from IPython.display import HTML, display
display(HTML(open('output/screener.html').read()))
```

**Sådan virker rangeringen (tre trin, holder API-kald nede):**

1. **Teknisk shortlist** (`technicals.py`, gratis — genbruger allerede hentet
   kursdata): for hver aktie beregnes trendfilter (kurs > SMA200 — samme
   regel som backtesten), golden/death cross (SMA50 ift. SMA200),
   52-ugers breakout, RSI-oversolgt-rebound/MACD-momentumskift og
   volumenspike. Kun aktier der består trendfilteret kan blive kandidater;
   de bedste `screener_shortlist_size` (default 20) går videre til trin 2.
2. **Analytiker- og sentimentdata** (`sentiment.py`, kun for shortlisten):
   - *Analytikere* (Yahoo Finance via yfinance): konsensus-kursmål,
     købs-/salgsanbefaling, seneste op-/nedgraderinger.
   - *StockTwits* (gratis, uautoriseret API): andel bullish/bearish-tags på
     seneste opslag. Dækker reelt kun amerikanske tickere/ADR'er — europæiske
     og nordiske hjemmemarkeds-tickere vil typisk vise "ingen data" her, det
     er forventet, ikke en fejl.
   - *Reddit* (kræver egen opsætning, se nedenfor): antal opslag der nævner
     aktien i udvalgte subreddits seneste uge, rangeret relativt til de andre
     kandidater i samme kørsel.
3. **Komposit-score** (`screener.py`): teknisk score og analytiker+sentiment-
   score vægtes sammen (default 50/50 — justér `screener_ta_weight` /
   `screener_sentiment_weight` i `config.py`). Mangler en aktie analytiker-/
   sentimentdata helt, får den en neutral score (50) i stedet for at blive
   straffet for manglende dækning.

**Opsætning af Reddit (valgfrit, gratis, ~2 minutter):**

1. Gå til [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) på en
   almindelig computer eller telefon, log ind, og opret en ny app —
   vælg type **"script"**. Navn og beskrivelse er ligegyldige; "redirect uri"
   kan sættes til `http://localhost`.
2. Du får et **client ID** (strengen lige under app-navnet) og en
   **secret**.
3. I Colab: klik nøgle-ikonet i venstre sidebjælke ("Secrets") → tilføj to
   secrets ved navn `REDDIT_CLIENT_ID` og `REDDIT_CLIENT_SECRET` med de to
   værdier → husk at slå "Notebook access" til for dem. Sådan undgår du at
   have dine Reddit-nøgler liggende i selve koden/gisten.
4. Kør `!pip install -q praw` i en celle før du kører screeneren.

Uden dette hop screeneren automatisk Reddit-delen over — ingen fejl, bare et
"ikke konfigureret" i rapporten.

**Begrænsninger specifikt for screeneren:**
- StockTwits/Reddit er "best effort": uofficielle eller rate-limitede
  API'er, der kan fejle eller mangle data for enkelte aktier uden at det
  vælter resten af kørslen. Jeg har ikke kunnet teste disse live herfra
  (intet internet i det sandbox-miljø værktøjet er bygget i) — hvis noget
  ser forkert ud eller fejler helt, så send mig fejlbeskeden.
  - "Upside" er enten analytiker-konsensus (når den findes) eller et rent
  teknisk estimat (afstand til 52-ugers højeste) — sidstnævnte er IKKE et
  analytisk skøn over fremtidigt afkast, kun en afstandsmåling.
- Screeneren er et øjebliksbillede af i dag — den er bevidst IKKE en del af
  no-lookahead-backtesten, fordi analytiker-/sentimentdata ikke findes
  pænt tidsstemplet bagud i tid på en måde der kan backtestes korrekt.

## Mine Aktier (mobil-app)

En Streamlit-app (`app.py`) med en enkel, "Værdipapirer"-agtig GUI: du
tilføjer aktier til en watchlist ved at skrive et frit navn (fx "novo") —
appen slår selv den rigtige Yahoo-ticker op (fx NOVO-B.CO) via Yahoo
Finances søgefunktion, og falder tilbage til at bruge det du skrev direkte,
hvis opslaget fejler. Tryk "Analysér watchlist", og få et
kort pr. aktie med kurs, dags-ændring, en mini-graf og en komposit-rating
(0-100) + upside-estimat, anvendt på én ticker ad gangen.

**Komposit-scoren vægter fire faktorer** (justerbart i `Config`-klassen i
`core.py`; oprindeligt tre faktorer — teknisk 30%/fundamental 25%/
popularitet 45% — nedskaleret proportionalt da Aktieguld blev tilføjet som
4. faktor, jf. bruger, 2026-09):
- **Teknisk (24%)**: kort bane (dage/uger — golden/death cross, 52-ugers
  breakout, RSI/MACD-momentumskift, volumenspike) vægtet 60%, og lang bane
  (måneder/år — 12-måneders prismomentum, nærhed til flerårs-højeste,
  baseret på 5 års historik) vægtet 40%.
- **Fundamental (20%)**: rigtige regnskabstal fra Yahoo Finance (P/E, PEG,
  overskudsgrad, omsætnings-/indtjeningsvækst, egenkapitalforrentning,
  gæld/egenkapital, og — tilføjet efter bruger-ønske, Genmab-eksempel,
  2026-09-18 — forward P/E vs. nuværende P/E, price/sales, frit cash
  flow-afkast og insiderejerskab) — IKKE bare analytikernes anbefaling som
  før. Dette er en simpel, sektor-uafhængig heuristik (en bank og en
  tech-aktie vurderes på samme skala) — brug som groft filter, ikke præcis
  værdiansættelse. **Bevidst fravalgt**: konkrete begivenheder/katalysatorer
  (fx FDA-godkendelser, fase 3-udlæsninger) — den slags data findes ikke
  gratis via Yahoo Finance, kun subjektiv/manuel indtastning ville kunne
  gøre det, hvilket brugeren selv fravalgte. Som den eneste tidsbestemte
  info vises næste regnskabsdato (hvis kendt) som en ren info-linje under
  "Detaljer"/"Triggere" — indgår ikke i nogen score.
- **Popularitet (36%, vægtet tungest)**: analytiker-anbefaling +
  StockTwits-sentiment + Reddit-omtale, som før — men nu med højere vægt,
  da tiltro/opmærksomhed har stor effekt på om et setup rent faktisk
  spiller ud.
- **Aktieguld (20%)**: en tilnærmet gengivelse af Jens Løgstrups bogmodel
  "Aktieguld" — se afsnittet nedenfor for metode og vigtige forbehold.

Mangler en aktie data på en faktor (fx ingen fundamental-dækning), får den
en neutral 50 på den faktor i stedet for at blive straffet.

### "Aktieguld"-point — tilnærmet gengivelse af Løgstrup-modellen

Efter bruger-ønske er der tilføjet en 4. faktor til komposit-scoren,
inspireret af Jens Løgstrups bog "Aktieguld" (4-fase-model: Strategisk
Analyse → Afkastberegning → Kvalitetspoint → Vedligehold/Konklusion).
Beregnes udelukkende fra Yahoo Finance-nøgletal (`core.py:get_aktieguld_data`)
— ikke AI-vurdering pr. aktie eller manuel indtastning, jf. bruger.

**Vigtigt forbehold — læs før du stoler på Aktieguld-tallet:** bogens fulde
metode er ikke offentligt tilgængeligt (tjekket via websøgning 2026-09-17 —
kun boghandler-sider og anmeldelser, ingen gengiver selve modellen), og
brugerens eget eksempel (en Royal Unibrew-analyse fra en separat AI-chat)
blev afbrudt lige før Fase 3's kriterieliste kunne læses. Det betyder:

- **Fase 1 (Strategisk Analyse) og Fase 2 (Afkastberegning)** bygger på
  ORDRETTE oplysninger fra brugerens eksempel — Fase 2's formel er ligefrem
  gengivet 1:1 (`Forventet afkast % = Overskudsvækst % + Aktietilbagekøb % +
  Udbytte %`, udregnet fra omsætnings-/indtjeningsvækst, tilbagekøb fra
  cashflow-opgørelsen og udbytteprocent). Fase 1's 5 spørgsmål er kendt
  ordret, men kun 4 af dem har en brugbar Yahoo-proxy (omsætningsvækst,
  overskudsgrad, indtjeningsvækst, egenkapitalforrentning som proxy for
  konkurrenceevne) — "vil produkterne være relevante om 5+ år" kan ikke
  udledes af et nøgletal og indgår derfor ikke.
- **Fase 3 (Kvalitetspoint)** er MIN EGEN FORTOLKNING, ikke bogens 7 punkter
  — de er aldrig blevet gengivet nogen steder. Bruger i stedet insider-/
  institutionelt ejerskab, gæld/egenkapital, udlodningsgrad og beta som
  stedfortrædere for stikordene "stærk ledelse, fokus, robusthed" fra
  brugerens eksempel.
- **Fase 4 (Vedligehold/Konklusion)** — bogens A/B/C/D-bogstavskonklusion
  er IKKE forsøgt genskabt (tærsklerne er ukendte). Den funktion varetages
  i stedet af appens eksisterende KØB/HOLD/SÆLG-anbefaling.

Sig til hvis du på et tidspunkt kan skaffe/indsætte den ordrette liste over
Fase 3's 7 kriterier eller Fase 4's tærskler (fx ved at gå tilbage til den
oprindelige chat, hvor teksten blev afbrudt) — så rettes tilnærmelsen til
den rigtige formel. Se de tre delscorer (Fase 1/2/3) og en kort begrundelse
under "Detaljer"/"Triggere, nøgletal og kilder" på hvert kort.

### Sektor-rotation — info-badge (påvirker IKKE komposit-scoren)

Efter bruger-ønske (jf. bruger, 2026-09-17) viser hvert kort nu også et
sektor-rotations-badge under "Detaljer"/"Triggere, nøgletal og kilder" —
udtrykkeligt bekræftet som et **rent informations-badge**: det tæller ikke
med i komposit-scoren, og de fire faktor-vægte (24/20/36/20 ovenfor) er
uændrede.

Metode (`core.py:get_sector_rotation_map` / `get_sector_signal`): en
sektors "rotation" måles som dens RELATIVE styrke — den amerikanske
sektor-ETF's kursafkast de seneste ~3 måneder minus verdensindekset (ACWI)
i samme periode. Er forskellen ≥ +3 procentpoint er sektoren "i medvind"
(🟢, slår markedet), er den ≤ -3 procentpoint er den "i modvind" (🔴,
halter efter); derimellem er den neutral (⚪). Aktiens sektor slås op via
Yahoo Finances GICS-klassifikation (fx "Technology", "Energy") og matches
til den tilsvarende amerikanske sektor-ETF (SPDR Select Sector-serien: XLK,
XLF, XLV, XLE, XLI, XLY, XLP, XLU, XLB, XLRE, XLC).

**Forbehold**: der findes ingen separate danske/europæiske sektor-ETF'er,
så for ikke-amerikanske aktier (herunder danske) bruges den globale
GICS-sektor som et proxy-signal — sektor-cyklusser hænger langt fra
perfekt sammen på tværs af markeder, men bruger har accepteret dette frem
for slet ingen sektor-info for hovedparten af sine aktier. Mangler aktiens
sektor (ukendt hos Yahoo) eller kunne ETF-dataene ikke hentes, udelades
badge'en helt i stedet for at vise et misvisende tal. Sektor-kortet
genberegnes højst hvert 1. time (cachet), da 3-måneders afkast alligevel
ikke ændrer sig fra minut til minut.

**KØB/HOLD/SÆLG-anbefaling**: ud over komposit-scoren viser hvert kort nu
også en klar handling. Sælg-signaler er en symmetrisk modpart til de
almindelige købs-triggere — death cross, brud under 200-dages glidende
gennemsnit, nyt 52-ugers laveste, RSI-rollover fra overkøbt, negativt
MACD-momentumskift — plus tre ekstra tjek: er kursen faldet under det
tekniske stop-loss-niveau (tvinger altid SÆLG, uanset resten af scoren),
er 12-måneders momentum tydeligt negativt, og er de fundamentale nøgletal
eller StockTwits-sentimentet svagt. Reglen: brudt trend eller brudt
stop-loss giver altid SÆLG; ellers SÆLG hvis nok sælg-signaler samler sig;
KØB kræver en høj komposit-score OG fravær af sælg-signaler; alt andet er
HOLD. Se "Detaljer"/"Triggere, nøgletal og kilder" på hvert kort for
begrundelserne. Fuldt regelbaseret og gennemsigtigt — ikke en ordre eller
en garanti.

En "Top 10"-fane scanner hele det kuraterede univers (samme ~93 aktier som
kandidat-screeneren bruger — ikke bogstaveligt "alle" aktier på Yahoo
Finance, det er ikke teknisk muligt uden at blive rate-limitet) og viser de
10 bedst rangerede kandidater efter samme komposit-score. Hver kandidat får
også et konkret handelsforslag: en indgangskurs (nuværende kurs) med et
indgangsvindue (`entry_window_days`, default 3 handelsdage — signalet
regnes som forældet derefter), en teknisk stop-loss (under seneste
kursbund eller 50-dages glidende gennemsnit, alt efter hvad der er lavest —
**denne regel er IKKE backtestet**, i modsætning til `stop_loss_pct` i
`config.py` som backtesten bruger), og en tidsbaseret "stopud dato"
(`swing_hold_days`, default 20 handelsdage — luk positionen her hvis
hverken mål eller stop er nået undervejs). Resultatet caches 30 minutter
og tager typisk 2-4 minutter at beregne første gang.

Der er også en "AI Chat"-fane; i denne version svarer den kun
med en fast tekst, fordi en rigtig AI-forbindelse kræver en betalt API-nøgle
— skelettet er der, så vi nemt kan koble en rigtig model på senere, hvis du
vil det.

**Stop-loss — "loft over tab"**: stop-niveauet (i Top 10 og i watchlist-
detaljerne) er det tekniske niveau (seneste kursbund eller 50-dages
glidende gennemsnit, alt efter hvad der er lavest) — MEN tabet må aldrig
blive større end 10% fra indgangskursen. Er det tekniske niveau dybere end
-10%, bruges -10%-loftet i stedet; findes intet teknisk niveau under
kursen, bruges -10%-loftet alene. Justér grænsen i `stop_loss_max_pct` i
`Config`-klassen i `core.py`, hvis 10% skal ændres.

Koden er siden delt i to filer: `core.py` (al analyselogik — teknisk,
fundamental, sentiment, komposit-score, handelsplan) og `app.py` (kun
GUI'en + caching). Det er en forudsætning for det automatiske daglige tjek
nedenfor, som genbruger præcis den samme logik uden at køre i Streamlit.
Begge filer skal ligge i samme GitHub-repo, som før.

Sådan får du den som en app på telefonen (gratis, ingen installation):

1. Upload `app.py` OG `core.py` til dit GitHub-repo (samme metode som du
   allerede har brugt: "Add file → Upload files"). Upload også den
   opdaterede `requirements.txt` på samme måde — den overskriver den
   gamle, hvis GitHub spørger.
2. Gå til https://share.streamlit.io og log ind med din GitHub-konto.
3. Vælg "New app", vælg dit repo (`zipo70/Investment`), branch `main`, og
   angiv `app.py` som hovedfil. Tryk "Deploy".
4. Du får en fast URL (noget i stil med `https://dit-navn.streamlit.app`).
   Åbn den i Safari på din iPhone, tryk på del-ikonet (firkant med pil op),
   og vælg "Føj til hjemmeskærm". Nu ligger den som et app-ikon du kan åbne
   direkte — uden at skulle gå via Colab eller GitHub.

**Vigtig begrænsning**: watchlisten gemmes i en simpel fil på Streamlits
server. Så længe appen er "vågen" (bruges jævnligt) holder den ved — men
efter lang tids inaktivitet kan Streamlit Community Cloud genstarte appen
"fra bunden" og nulstille watchlisten. Til en midlertidig research-app er
det en fair pris for at være gratis. Det er også præcis derfor det
automatiske daglige tjek nedenfor bruger sin egen liste
(`watchlist_tickers.txt`, gemt permanent i GitHub-repoet) i stedet for
app'ens watchlist.

## Automatisk dagligt tjek (push-notifikation ved KØB/SÆLG)

Appen kører kun, når nogen har den åben i browseren — den kan ikke selv
tjekke aktier i baggrunden. Løsningen er `alert_check.py`, et lille script
der genbruger præcis samme analyselogik (via `core.py`), kørt automatisk
hver hverdagsmorgen af en gratis GitHub Actions-workflow
(`.github/workflows/daily_alert.yml`) — helt uafhængigt af om du har appen
åben på telefonen.

**Hvad den tjekker:** alle tickere i `watchlist_tickers.txt` (en simpel
tekstfil i repoet, som du selv redigerer på GitHub) PLUS de 10 aktuelle
Top 10-kandidater — automatisk, hver dag. Er der mindst ét KØB- eller
SÆLG-signal, sendes én samlet notifikation. Er alt HOLD, sker der intet —
for ikke at spamme dig med daglige "intet nyt"-beskeder.

**Om notifikationen — vigtigt at vide:** der findes ikke nogen gratis
tjeneste, der pålideligt kan sende rigtige sms'er til danske numre — alle
rigtige SMS-udbydere (fx Twilio) koster nogle øre pr. besked. Det gratis
alternativ er en push-notifikation (via [ntfy.sh](https://ntfy.sh)), som i
praksis fungerer helt som en sms: den dukker op på din telefons låseskærm
med lyd, uden at du behøver have nogen app åben. Værktøjet er sat op til
ntfy som standard. Vil du hellere have en rigtig sms-tekstbesked til dit
nummer, er Twilio understøttet som alternativ (se nedenfor) — det koster
typisk under 50 øre pr. besked, ingen abonnement.

**Opsætning — gratis push-notifikation via ntfy (anbefalet, ~2 minutter):**

1. Installér appen **ntfy** (gratis, findes til iPhone i App Store).
2. Åbn appen, tryk "+" og abonnér på et selvvalgt, langt og hemmeligt
   kanalnavn — fx `jacobi-mine-aktier-8k2f9x` (alle der kender kanalnavnet
   kan sende til det, så gør det langt og tilfældigt, ikke noget
   forudsigeligt).
3. På GitHub: gå ind i dit repo → **Settings → Secrets and variables →
   Actions → New repository secret**. Navn: `NTFY_TOPIC`. Værdi: samme
   kanalnavn som i trin 2. Gem.
4. Upload `alert_check.py`, `core.py`, `watchlist_tickers.txt` og mappen
   `.github/workflows/daily_alert.yml` til repoet (samme "Add file →
   Upload files"-metode; GitHub bevarer selv mappestrukturen fra zip'en,
   eller du kan oprette filerne enkeltvis og indsætte indholdet).
5. Rediger `watchlist_tickers.txt` og skriv de tickere, du vil holde øje
   med udover Top 10 — én pr. linje.
6. Under "Actions"-fanen på GitHub kan du trykke "Run workflow" for at
   teste med det samme, i stedet for at vente til næste morgen.

**Opsætning — rigtig sms via Twilio (valgfrit, koster lidt):**

1. Opret en gratis konto på [twilio.com](https://www.twilio.com), verificér
   dit nummer, og køb (eller brug prøvekredit til) et afsender-nummer.
2. Tilføj disse fire GitHub-secrets (samme sted som ovenfor):
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (Twilio-
   nummeret) og `TWILIO_TO_NUMBER` (dit eget nummer, fx `+4521731191`).
3. Er alle fire Twilio-secrets sat, bruges Twilio automatisk i stedet for
   ntfy — du behøver ikke fjerne `NTFY_TOPIC`.

**Tidspunkt:** workflowet kører kl. 06:00 UTC på hverdage (mandag-fredag),
svarende til kl. 08:00 dansk sommertid / kl. 07:00 dansk vintertid — efter
at både de amerikanske og europæiske børser er lukket for dagen forinden.
Rediger cron-linjen i `.github/workflows/daily_alert.yml` hvis du vil have
et andet tidspunkt.

**Begrænsninger:**
- Dette er, ligesom resten af "Mine Aktier", regelbaserede forslag til
  videre research — ikke en ordre eller en garanti.
- ntfy-kanalen er ikke adgangskontrolleret ud over selve kanalnavnet —
  brug et langt, tilfældigt navn, ikke noget let at gætte.
- GitHub Actions' gratis niveau har rigeligt med kørselstid til dette
  (et par minutter, én gang dagligt, på et privat/lille repo) — ingen
  omkostning at forvente.

## Vigtige begrænsninger — læs før du stoler på tallene

- **Valuta ikke modelleret**: porteføljen blander USD/EUR/DKK/SEK/NOK/CHF/GBP-
  aktier og summerer deres lokale procentafkast uden FX-konvertering.
  Nordnets vekslingsgebyr ved handel i fremmed valuta (typisk ~0,25–0,5%) er
  IKKE med. Til reel handel skal dette lægges ind.
- **Overlevelsesbias**: universet er dagens likvide aktier — selskaber der er
  gået konkurs eller er afnoteret i backtest-perioden mangler.
- **Eksekvering til dagens slutkurs** på beslutningsdagen — ingen slippage
  eller næste-dags-udførelse modelleret.
- **Sharpe-ratio** antager 0% risikofri rente.
- Historisk performance er INGEN garanti for fremtidig performance.

## Foreslåede næste skridt

1. Kør backtesten, gennemgå `output/report.html` sammen med mig — vi kan
   justere lookback-vinduer, stop-loss, universe eller vægtningsmetode
   (`config.py: weighting = "equal" | "inverse_vol"`) og genkøre.
1b. Kør kandidat-screeneren (`run_screener.py`) op til hver rebalancering for
   at få en rangeret liste med begrundelser — se afsnittet "Kandidat-screener"
   ovenfor.
2. Når du er tilfreds med backtest-resultaterne: byg en simuleret
   paper-trading-loop, der "handler" fremadrettet på nye data uden rigtige
   penge, og følg den i praksis over noget tid (jo længere jo bedre — helst
   flere kvartaler).
3. Kun hvis den simulerede performance over tid ser robust ud: overvej
   manuel eksekvering af agentens forslag på Nordnet — dvs. AGENTEN
   FORESLÅR, DU TRYKKER KØB/SÆLG. Fuldautomatisk ordreafgivelse mod en
   rigtig konto er bevidst uden for scope her.

## Filoversigt

| Fil | Ansvar |
|---|---|
| `universe.py` | Aktieunivers pr. region + benchmark |
| `config.py` | Alle strategi-/backtest-parametre |
| `data_fetch.py` | Henter og cacher kursdata via yfinance |
| `strategy.py` | Trend/momentum-signal og positionsvægte |
| `backtest.py` | Porteføljesimulering (rebalancering, stop-loss, omkostninger) |
| `metrics.py` | CAGR, max drawdown, Sharpe, kvartalsafkast, handelsstatistik |
| `report.py` | Genererer `output/report.html` |
| `run_backtest.py` | Kør backtesten |
| `technicals.py` | Tekniske triggere til screeneren (golden cross, breakout, RSI/MACD, volumen) |
| `sentiment.py` | Analytiker-, StockTwits- og Reddit-data til screeneren |
| `screener.py` | Kandidat-rangering (TA + analytiker + sentiment) |
| `screener_report.py` | Genererer `output/screener.html` |
| `run_screener.py` | Kør kandidat-screeneren |
| `core.py` | Delt analyselogik for "Mine Aktier" (teknisk, fundamental, sentiment, komposit-score, handelsplan) — bruges af både `app.py` og `alert_check.py` |
| `app.py` | "Mine Aktier" — Streamlit-GUI/mobil-app (watchlist + rating + Top 10) |
| `alert_check.py` | Automatisk dagligt KØB/SÆLG-tjek, kørt af GitHub Actions — sender push-notifikation/sms |
| `watchlist_tickers.txt` | Ticker-liste (redigeres på GitHub) som `alert_check.py` tjekker udover Top 10 |
| `.github/workflows/daily_alert.yml` | GitHub Actions-workflow der kører `alert_check.py` hver hverdagsmorgen |

Kun til eget analysebrug — ikke finansiel rådgivning.
