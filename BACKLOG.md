# Backlog — kendte problemer / opgaver

Formål: notere bruger-erfaringer og fejl efterhånden som de opdages under
brug, så intet glemmes, uden at rette dem med det samme. Ryddes op i takt
med at punkterne rettes (flyt til "Rettet" med dato, eller slet).

## Åbne opgaver
*(ingen lige nu — se "Vigtigt at vide" under punkt 6 og 2 i "Rettet" for ting der stadig kræver din stillingtagen/test)*

## Rettet

### 1. Top 10-scanning brugte meget tid på tickere der ikke kunne findes — RETTET 2026-08-28
`fetch_ticker_df()` i `core.py` prøvede 3 gange pr. ticker med stigende
ventetid (op til 6 sekunder), også når svaret var tomt (dvs. tickeren
reelt ikke findes/er afnoteret) — hvor gentagne forsøg aldrig hjælper.
Rettet til: et tomt svar UDEN exception springes nu over med det samme,
uden ventetid. Kun rigtige exceptions (netværksfejl, timeout) beholder de
3 forsøg med stigende ventetid, da et nyt forsøg der faktisk kan give
mening. Testet med både en "tom respons"-case (0 sekunder, 1 forsøg) og
en "rigtig netværksfejl"-case (3 forsøg, ventetid som før).

### 2. Ticker-navneopslag ("skriv 'novo', få forslag til børs") virkede ikke længere — RETTET 2026-08-28
`resolve_ticker()` prøver stadig Yahoo Finances søge-API først, men fejler
nu ikke længere stille — status/fejl logges (synligt i appens driftslog
via "Manage app" på Streamlit Cloud). Vigtigere: der er tilføjet en
indbygget navn→ticker-liste for hele det kuraterede univers (~93 aktier,
`_NAME_ALIASES` i `core.py`), som bruges som fallback hvis Yahoo-søgningen
fejler eller intet finder. "novo" → NOVO-B.CO virker nu uafhængigt af om
Yahoos uofficielle søge-API er oppe. Testet isoleret (matcher "novo",
"NOVO-B.CO" og "mærsk" korrekt, afviser en opdigtet tekst).
**Vigtigt at vide:** jeg har ikke kunnet teste selve Yahoo-søgningen live
herfra — miljøet jeg arbejder i har ikke netadgang til Yahoo Finance (kun
til pakke-registre). Så jeg ved ikke med sikkerhed OM Yahoo-søgningen
faktisk var blokeret, eller om der var en anden årsag — men uanset hvad,
er "novo" og de øvrige ~93 aktier i universet nu robuste over for det,
takket være fallback-listen. Sig til hvis opslag på aktier UDENFOR
universet (som Yahoo-søgningen ellers dækkede) stadig driller — så kigger
vi videre på selve Yahoo-delen.

### 3. Top 10 viste kun ~3 kandidater i stedet for 10 (testet på mobil) — DELVIST RETTET 2026-08-28
To ting rettet, som tilsammen mindsker risikoen for at ramme Yahoos
rate-limit under en scanning:
- Tilføjet en kort høflighedspause (0,3 sek.) mellem hvert kald i trin 1
  (fuld-univers-scanningen) — havde ingen pause før, i modsætning til
  trin 2. Gør scanningen et par sekunder langsommere i alt, men mindsker
  risikoen for at mange kald fejler på stribe.
- Punkt 1's rettelse (ingen ventetid ved "findes ikke") betyder også
  færre samlede HTTP-kald ved gentagne "døde" tickere.
- Tilføjet en tydelig advarsel i Top 10-fanen, hvis andelen af "kunne ikke
  hentes" overstiger 20% af universet — så det fremover er tydeligt om et
  lavt antal kandidater skyldes rate-limitering eller bare et roligt
  marked, i stedet for at skulle gætte.
**Vigtigt at vide:** dette kan ikke garantere at Yahoo aldrig rate-limiter
— det er stadig deres uofficielle/gratis grænseflade. Kør gerne en ny
scanning og se om antallet af kandidater er højere nu, og hold øje med
den nye advarsel hvis det sker igen.

### 4. Nøgletal/triggere i "Detaljer"-undermenuen så grimme ud — RETTET 2026-08-28
`st.write("Label:", liste)` (som viste rå Python-listesyntaks, fx
`['Death cross for 3 dage siden', ...]`) er erstattet af en fælles
hjælpefunktion (`_render_triggers()` i `app.py`), der viser en lille
overskrift med ikon + en rigtig punktopstilling — samme sted i BÅDE
watchlist-kortenes "Detaljer" og Top 10-kortenes "Triggere, nøgletal og
kilder". Ikoner: 🔴 sælg-signaler, 📈 tekniske triggere (kort bane), 📊
langsigtede triggere (lang bane), 💰 fundamentale styrker, 🎯
analytiker-anbefaling. Testet med `AppTest` — bekræftet at raw
liste-syntaks ikke længere optræder, og at punkterne vises korrekt.

### 5. Top 10-listen kunne ikke sorteres på forskellige måder — RETTET 2026-08-28
Tilføjet en sorterings-vælger øverst i Top 10-fanen med syv muligheder:
komposit-score, upside %, teknisk score, fundamental score,
popularitet/analytiker-tiltro, region og alfabetisk (ticker). Sorteringen
sker på det allerede hentede resultat — ingen ny scanning nødvendig ved
skift. (Undervejs opdagede jeg at popularitets-scoren ikke blev gemt i
resultatet nogen steder — tilføjet som `popularity_score`-feltet, så den
nu er tilgængelig både til sortering og til fremtidig visning.) Testet
med `AppTest`: bekræftet at rækkefølgen faktisk ændrer sig korrekt ved
skift mellem "Komposit-score" og "Upside %".

### 7. Upside var stadig uklar/misvisende ved manglende analytikerdækning — RETTET 2026-08-29
Efter yderligere feedback ("kan ikke se hvad jeg skal bruge upside til...
misvisende hvis aktien ikke dækkes"): den tekniske fallback ("afstand til
52-ugers højeste", brugt når ingen analytikere dækker aktien) er helt
fjernet fra `compute_upside()` i `core.py`. Upside vises nu KUN når der er
rigtig analytiker-konsensus bag tallet. I `app.py` er "Upside: ..."-linjen
(både i watchlisten og Top 10) nu udeladt helt (blank) i stedet for at
vise "–" eller et misvisende teknisk gæt, når aktien ikke er dækket.
Testet med `AppTest`: en dækket aktie viser fortsat "Upside: -5.0% (…)",
en udækket aktie viser kun kursen, ingen Upside-linje.

### 6. Kandidater med negativ upside kan alligevel ende højt i Top 10 — RETTET (Mulighed B) 2026-08-28
Som beskrevet krævede dette et designvalg mellem tre muligheder (lade
upside indgå i scoren, forklare det tydeligere, eller filtrere negative
kandidater fra). **Jeg har valgt Mulighed B** (den mindst indgribende):
komposit-scoren er uændret — den måler stadig momentum/kvalitet/
popularitet, ikke "hvor billig aktien er" — men der vises nu en tydelig
forklaring, hver gang upside er negativt (både i watchlisten og Top 10):
"⚠️ Negativ upside betyder at kursen allerede ligger over analytikernes
gennemsnitlige kursmål — det er uafhængigt af komposit-scoren...".
**Sig til hvis du hellere vil have Mulighed A** (lad upside veje med i
selve scoren — kræver en ny vægtningsdiskussion) **eller Mulighed C**
(skjul/nedton kandidater med negativ upside i Top 10) — det er hurtigt at
lave om, nu hvor grundstrukturen er der.
