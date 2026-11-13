# Paper trading-bot

Een bot die met **nep-geld** handelt om één vraag te beantwoorden:

> Doet een strategie het beter dan simpelweg de markt kopen en vasthouden?

Er wordt nergens met echt geld gehandeld. Er zit geen koppeling met een broker
in deze code en die komt er ook niet in. Een test bewaakt dat
(`tests/test_geen_echt_geld.py`): die leest alle code en valt om zodra er een
opdracht in staat waarmee je een order zou kunnen plaatsen of geld verplaatsen.

**Dit is geen beleggingsadvies.** Wat hier uitkomt is een meting op nep-geld,
geen aanbeveling om iets te kopen of te verkopen.

---

## Wat er nu klaar is (stap 3 van 6)

| Onderdeel | Status |
|---|---|
| Koersen ophalen (aandelen, ETF's, crypto, wisselkoers) | klaar |
| De drie regelstrategieën | klaar |
| De meetlat: kopen en vasthouden van SPY en BTC | klaar |
| De nep-broker met kosten en slippage | klaar |
| Backtest over het afgelopen jaar | klaar |
| Logboek in SQLite | klaar |
| Dagelijkse ronde, veilig om twee keer te draaien | klaar |
| Overzicht van de stand | klaar |
| AI-analist (Claude), met controleportefeuille | klaar, wacht op je API-key |
| Kostenmeting en maandprognose van de AI | klaar |
| Automatisch draaien via GitHub Actions | stap 4 |
| Telegram-meldingen | stap 4 |
| Weekrapport en challenger | stap 5 |
| Streamlit-dashboard | stap 6 |

---

## Beginnen

Je hebt Python nodig (versie 3.11 of hoger) en verder niets.

**1. De omgeving klaarzetten** (eenmalig, is al gedaan op deze computer):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**2. Koersen ophalen.** Dit duurt ongeveer een minuut en haalt 3 jaar
geschiedenis op voor alle 48 symbolen:

```bash
.venv\Scripts\python.exe scripts\vernieuw_data.py --jaren 3
```

**3. De dagelijkse ronde draaien.** De eerste keer begint hiermee de echte
meting: elke portefeuille krijgt €1.000 nep-geld.

```bash
.venv\Scripts\python.exe scripts\dagelijks.py
```

**4. Kijken hoe het ervoor staat:**

```bash
.venv\Scripts\python.exe scripts\stand.py --trades
```

**5. De backtest draaien** (mag, maar is geen bewijs; zie verderop):

```bash
.venv\Scripts\python.exe scripts\backtest.py
```

**6. De tests draaien** (doe dit na elke wijziging):

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

### De dagelijkse ronde

`scripts/dagelijks.py` is het script dat straks elke nacht vanzelf draait. Het
haalt de nieuwste koersen op, laat elke strategie beslissen, voert de orders van
gisteren uit en legt de stand vast. Je kunt het zonder zorgen opnieuw draaien:

* **Twee keer op dezelfde dag?** Een dag die al verwerkt is, wordt
  overgeslagen. Er komen nooit dubbele trades.
* **Een paar dagen niets gedraaid?** Die dagen worden vanzelf ingehaald, in de
  goede volgorde.
* **Halverwege een storing?** Alles van één dag gaat in één keer de database in.
  Gaat het mis, dan wordt die hele dag teruggedraaid en blijft er niets half
  staan.

Opnieuw beginnen kan met `scripts\herstart_meting.py`. Dat wist alle
portefeuilles en trades, dus je bent je meting kwijt. Het vraagt eerst om
bevestiging.

---

## Hoe het werkt, in gewone taal

Elke strategie heeft een eigen portefeuille van **€1.000 nep-geld**. Per trade
gaat er maximaal **€100** in, dus een strategie heeft hooguit tien posities
tegelijk en zit vaak deels in contanten.

De dagindeling is altijd dezelfde:

1. De strategie kijkt naar de koersen **tot en met vandaag** en besluit wat hij
   wil kopen of verkopen.
2. Die order wordt uitgevoerd op de **openingskoers van de eerstvolgende
   handelsdag**, met 0,05% slippage en 0,1% kosten.
3. Aan het eind van de dag wordt de waarde van de portefeuille vastgelegd.

Stap 1 en 2 staan met opzet uit elkaar. Zo kan geen enkele strategie een koers
gebruiken die hij op het moment van beslissen nog niet kon weten. Dat heet
"look-ahead" en het is de meest voorkomende manier waarop een backtest zichzelf
voor de gek houdt. `tests/test_geen_vooruitkijken.py` controleert het.

### De strategieën

| Naam | De gedachte erachter |
|---|---|
| **Momentum** | Koop de 8 symbolen die de afgelopen 60 beursdagen het hardst stegen. Verkoop zodra een naam uit de top 16 valt. |
| **Mean reversion** | Koop wat te hard gedaald is (RSI onder 30). Verkoop bij herstel (RSI boven 55) of na 20 beursdagen. |
| **Trend volgend** | Koop als het 20-daags gemiddelde omhoog door het 100-daags gemiddelde kruist. Verkoop als het weer eronder zakt. |
| **Meetlat SPY** | Koop op dag 1 voor het hele bedrag SPY en doe daarna niets meer. |
| **Meetlat BTC** | Hetzelfde met bitcoin. |

### Wat er gevolgd wordt

De regelstrategieën volgen **48 symbolen**: 30 grote aandelen, 10 indexfondsen
(ETF's) en 8 cryptoparen. Die lange lijst is er met een reden: hoe meer trades,
hoe sneller je kunt zien of een resultaat echt is of toeval.

De AI-analist volgt **15 symbolen**, omdat elke beslissing daar geld kost bij de
Claude-API.

Alles staat in `config.yaml`. Je kunt daar namen toevoegen of weghalen zonder
ook maar één regel code aan te raken.

### Alleen afgesloten dagen

De bot gebruikt alleen koersen van dagen die echt afgelopen zijn. Een aandelendag
is af om 17:00 in New York (een uur na sluiting), een cryptodag om middernacht
UTC. Een dag die nog bezig is, heeft als "slotkoers" gewoon de koers van dat
moment; daarop beslissen zou betekenen dat je op een halve dag handelt. Zo'n dag
wordt niet opgehaald, en als hij er toch staat, wordt hij weggehaald.

---

## De AI-analist

De opzet is geleend van TradingAgents: vijf rollen die samen tot een besluit
komen.

| Rol | Doet | Model |
|---|---|---|
| Technisch analist | leest de kengetallen (RSI, gemiddeldes, rendement) | Claude Haiku 5.5 |
| Nieuwsanalist | leest de nieuwskoppen en scheidt nieuws van ruis | Claude Haiku 5.5 |
| Bull | geeft het sterkste eerlijke argument voor kopen | Claude Sonnet 5.5 |
| Bear | geeft het sterkste eerlijke argument ertegen | Claude Sonnet 5.5 |
| Risk manager | hakt de knoop door: KOOP, VERKOOP of NIETS | Claude Sonnet 5.5 |

Elke beslissing krijgt een korte onderbouwing in gewone taal en een
zekerheidsscore. Alles wordt bewaard in de tabel `ai_besluiten`: wat elke rol
zei, en **welke nieuwskoppen de AI zag, met hun publicatietijd**.

### Twee AI-portefeuilles

* **`ai_analist`** werkt zoals in de tabel hierboven.
* **`ai_controle_haiku`** krijgt exact dezelfde instructies en dezelfde data,
  maar doet alles op het goedkoopste model.

Zo zie je na een paar maanden of het duurdere model zijn geld waard was. Dat
weet nu niemand: beter redeneren is niet hetzelfde als meer verdienen, en
koersen op korte termijn zijn grotendeels ruis.

### Regels die de bot zelf afdwingt

Een instructie aan een model is een verzoek. Deze dingen zijn regels in de code:

* Niet kopen wat je al hebt, niet verkopen wat je niet hebt, niet kopen zonder kas.
* Onder een zekerheid van 0,6 wordt er niet gehandeld.
* Een antwoord dat nergens op slaat, afgekapt is of geweigerd wordt, betekent:
  niets doen. De reden komt in het logboek.
* Zien beide analisten niets bijzonders en is er geen positie, dan wordt het
  dure debat overgeslagen. Is er wel een positie, dan wordt er altijd gedebatteerd.
* Een geweigerde vraag wordt niet stil naar een ander model doorgestuurd. Dan
  zou je analist midden in de meting ongemerkt iemand anders worden.

### Geen nieuws uit de toekomst

De AI-analist ziet alleen koppen die vóór het beslismoment zijn gepubliceerd.
yfinance geeft alleen de tien nieuwste berichten per symbool en er is geen
nieuwsgeschiedenis. Daarom:

* doet de AI-analist **niet mee in de backtest**;
* beslist hij bij het inhalen van gemiste dagen **alleen over de nieuwste dag**.
  Een besluit over drie dagen geleden met het nieuws van vandaag zou vooruitkijken
  zijn.

### Kosten

Het budget staat in `config.yaml` op **$5,50 per maand** (ongeveer €5). Twee
dingen houden de kosten in de hand:

* **Nooit twee keer betalen.** Een beslissing die al bestaat, wordt niet opnieuw
  gevraagd. Draait het script twee keer op een dag, dan kost de tweede keer niets.
* **Elke aanroep wordt geboekt** in de tabel `ai_kosten`, met de prijzen uit
  `config.yaml`.

Het doel is dat de AI de hele maand meedoet, zodat elke maand vergelijkbaar is.
Daarom kijk je na de eerste drie rondes met dit script wat een dag echt kost:

```bash
.venv\Scripts\python.exe scripts\ai_kosten.py
```

Past het niet binnen het budget, dan stelt het script aanpassingen voor die de
hele maand gelijk houden (minder symbolen, het debat alleen bij een sterk
signaal, de controleportefeuille uit), met wat elk scheelt. Het verandert zelf
niets. Mocht het budget onverhoopt toch op raken, dan stopt de AI-analist netjes
tot de volgende maand en lopen de andere strategieën door. Zet in de
Anthropic-console een iets hogere limiet ($6) als noodrem.

### De sleutel

Maak een API-key aan in de Anthropic-console en zet hem in `.env`, achter
`ANTHROPIC_API_KEY=`. Dat bestand staat in `.gitignore` en gaat nooit naar
GitHub. Plak de sleutel nergens anders, ook niet in een chat. Zonder sleutel
slaat de dagronde de AI-analist over en draait de rest gewoon.

---

## Eerlijk zijn over wat dit wel en niet zegt

### Een backtest bewijst niets

De backtest kijkt achteruit met regels die we nu hebben gekozen. Dat ziet er
bijna altijd mooier uit dan wat je vooruit haalt. Hij staat hier voor twee
andere dingen: controleren of de motor goed rekent, en zien hoeveel trades een
strategie ongeveer maakt. **De echte test begint vanaf de dag dat de bot
dagelijks gaat draaien.**

### "Betekenisvol" of nog gewoon geluk

Na elke run krijg je per strategie te horen of het resultaat al iets betekent.
Dat gaat zo: we nemen het resultaat in euro's van elke afgeronde trade en
trekken daar 10.000 keer een nieuwe steekproef uit (een "bootstrap"). Blijft het
gemiddelde dan bijna altijd positief, dan is het waarschijnlijk geen toeval.

Daarnaast rekent de bot uit **hoeveel trades er nog nodig zijn** voor een harde
uitspraak. Dat getal valt vaak tegen. In de backtest van november 2026 kwam er
voor momentum ongeveer 2.400 trades uit. Dat is geen fout in de berekening: het
betekent dat het verschil klein is ten opzichte van de schommeling, en dat je
dan heel veel waarnemingen nodig hebt. Wees daar niet door ontmoedigd, maar wees
er ook niet blind voor.

### De meetlat heeft een voordeel, en dat is bekend

De meetlat zet €1.000 in één keer in de markt. De strategieën mogen maar €100
per trade inleggen en hebben dus vaak geld in kas staan dat niets doet. In een
stijgende markt is dat een nadeel voor de strategieën dat niets met de kwaliteit
van hun regels te maken heeft. Dat is een bewuste keuze, want anders meet je niet
wat "de markt kopen en vasthouden" echt oplevert. Houd het in je achterhoofd bij
het lezen van het verschil.

### Vereenvoudigingen in de nep-broker

* **Fractionele stukken.** De bot koopt voor precies €100, ook als dat 0,065
  aandeel ASML is. Bij een echte broker kan dat lang niet altijd.
* **USDT telt als dollar.** De cryptokoersen staan in USDT. Die wijkt in de
  praktijk een fractie af van de dollar; dat verschil negeren we.
* **Wisselkoers in het weekend.** De valutamarkt is dan dicht, crypto niet. Voor
  zo'n dag gebruiken we de laatst bekende EUR/USD-koers.
* **Dividend telt niet mee.** We rekenen met koersen zonder dividend. Dat drukt
  het resultaat van aandelen en ETF's, ook dat van de meetlat SPY, dus de
  vergelijking tussen beide blijft eerlijk.
* **Geen belasting.** Er wordt geen vermogensbelasting of dividendbelasting
  gerekend.

### Hoe risico wordt gemeten

De Sharpe ratio en de grootste daling worden gerekend op één gemeenschappelijke
kalender: de dagen dat de Amerikaanse beurs open was. Anders zou een portefeuille
met crypto (die altijd handelt) niet eerlijk te vergelijken zijn met een
portefeuille vol aandelen (die in het weekend stilstaat). Cryptobewegingen in het
weekend gaan niet verloren; die zitten in de stap van vrijdag naar maandag.

---

## Waar alles staat

```
paper-trading-bot/
├── config.yaml              alle instellingen en de watchlists
├── .env.example             voorbeeld voor je sleutels (.env gaat nooit in git)
├── requirements.txt
├── data/
│   ├── trading.db           de echte database
│   └── backtest.db          de uitkomst van de laatste backtest
├── bot/
│   ├── config.py            instellingen inlezen
│   ├── database.py          de tabellen
│   ├── markt.py             koershistorie, zonder vooruitkijken
│   ├── indicatoren.py       RSI, gemiddeldes, rendement
│   ├── portefeuille.py      de nep-broker
│   ├── motor.py             laat alles dag na dag draaien
│   ├── statistiek.py        rendement, risico en "is dit al iets?"
│   ├── opzet.py             zet alle strategieën klaar
│   ├── overzicht.py         teksten over de stand (ook voor Telegram)
│   ├── data/                koersen, wisselkoers en nieuws ophalen
│   ├── strategieen/         de strategieën en de meetlat
│   └── ai/                  de AI-analist
│       ├── rollen.py        de vijf rollen: instructies en antwoordvorm
│       ├── analist.py       het debat en de harde controles
│       ├── ronde.py         de dagelijkse denkronde
│       ├── client.py        de verbinding met de Claude-API
│       ├── budget.py        de budgetbewaking
│       └── kostenprognose.py  past het in een maand?
├── scripts/
│   ├── vernieuw_data.py     koersen ophalen
│   ├── dagelijks.py         de dagelijkse ronde
│   ├── stand.py             laat zien hoe het ervoor staat
│   ├── ai_kosten.py         wat de AI echt kost
│   ├── backtest.py          de backtest
│   └── herstart_meting.py   opnieuw beginnen (wist alles)
└── tests/                   96 tests
```

### Wanneer begint de meting

De eerste keer dat je `dagelijks.py` draait, onthoudt de bot de startdatum. Die
wordt gezet op de laatste dag dat de Amerikaanse beurs open was, en niet
zomaar op vandaag. Anders zou de meting kunnen beginnen op een dag dat alleen
crypto handelde, en gingen de aandelenstrategieën en de meetlat SPY een dag
later van start dan de rest. Nu beginnen ze allemaal tegelijk.

---

## Veiligheid

* **Sleutels staan nooit in de code.** Lokaal in `.env` (die staat in
  `.gitignore`), op GitHub in Secrets.
* **Deze repository is openbaar.** Iedereen kan de code en de nep-resultaten
  zien. Je sleutels zijn niet zichtbaar, want die staan er niet in. Zet er dus
  ook nooit iets persoonlijks in.
* **ccxt wordt zonder sleutels gebruikt**, dus alleen voor publieke koersen.
  Een test controleert dat er alleen leesopdrachten in staan.
* **De bot leest alleen de sleutels voor Claude en Telegram.** Een test valt om
  zodra er ergens een andere sleutel wordt gelezen, bijvoorbeeld een van een
  beurs of broker.
* **De Claude-sleutel gaat niet door de code van de bot.** De officiële
  bibliotheek van Anthropic leest hem zelf uit de omgeving, zodat hij nergens in
  een logregel of foutmelding kan belanden.
* **Vaste versies.** `requirements.txt` legt de versie van elk pakket vast,
  zodat een update halverwege de meting niet ongemerkt iets verandert.
