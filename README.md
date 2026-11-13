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

## De hoofdvraag, vooraf vastgelegd

Wie achteraf kiest welke vergelijking telt, vindt altijd wel iets dat
"werkt". Daarom staat hier, voor de start, precies wat de meting moet
beantwoorden. Dit verandert tijdens de meting niet.

| | |
|---|---|
| **Vraag** | Verslaat `ai_analist` de gelijk verdeelde kopen-en-vasthouden-meetlat van zijn eigen symbolen? (`gelijk_alle` als de AI alle symbolen volgt, anders `gelijk_ai`) |
| **Maatstaf** | Het verschil in rendement, in dollars, inclusief dividend en na kosten en slippage |
| **Toets** | Een blok-bootstrap op de dagelijkse verschillen, met blokken van 10 beursdagen. "Betekenisvol" is een p-waarde van 0,05 of lager |
| **Start** | maandag 23 november 2026 (lukt dat niet, dan vrijdag 27 november) |
| **Einde** | dinsdag 23 februari 2027 |

Alles daarnaast is **verkennend**: de regelstrategieën, de meetlatten SPY en BTC,
het Haiku-controlepotje, `ai_enkel`, de weekrapporten. Interessant om te zien,
maar daar trek je geen harde conclusie uit. Een weekrapport is een tussenstand.

**Eerlijke verwachting:** drie maanden is kort. De kans is groot dat het verschil
aan het eind niet te onderscheiden is van toeval. Dat is dan ook een uitkomst:
het betekent dat er geen voordeel te zien was dat groot genoeg is om te tellen.

---

## Wat er nu klaar is (stap 5 van 6)

| Onderdeel | Status |
|---|---|
| Koersen ophalen (aandelen, ETF's, crypto), met dividend en splitsingen | klaar |
| De drie regelstrategieën | klaar |
| De meetlatten: SPY, BTC, en gelijk verdeeld over alle symbolen | klaar |
| De nep-broker met kosten, slippage, dividend en splitsingen | klaar |
| Backtest over het afgelopen jaar | klaar |
| Logboek in SQLite | klaar |
| Dagelijkse ronde, veilig om twee keer te draaien | klaar |
| AI-analist (Claude): een kans per symbool, de code beslist | klaar |
| Kostenmeting en maandprognose van de AI | klaar |
| Elke nacht automatisch draaien via GitHub Actions | klaar |
| Dagelijks bericht via Telegram | klaar |
| Weekrapport met conclusies van Claude | klaar |
| Challenger: een verbetering eerst toetsen, dan eerlijk testen, pas na jouw akkoord | klaar |
| Dashboard | stap 6 |

---

## Automatisch draaien: wat jij eenmalig doet

De bot draait elke nacht om **00:30 UTC** (01:30 in de winter, 02:30 in de
zomer) vanzelf op GitHub. Daarvoor moet je drie dingen eenmalig regelen.

### 1. Een Telegram-bot aanmaken

1. Open Telegram en zoek **@BotFather** (let op het blauwe vinkje).
2. Stuur `/newbot` en volg wat BotFather vraagt: eerst een naam, daarna een
   gebruikersnaam die op `bot` eindigt.
3. Je krijgt een sleutel (een lange code met een dubbele punt erin). Zet die in
   `.env`, achter `TELEGRAM_BOT_TOKEN=`. Plak hem nergens anders.
4. Zoek je nieuwe bot op in Telegram en stuur hem een berichtje, bijvoorbeeld
   `hoi`. Pas dan kan hij jouw chat vinden.
5. Draai:

```bash
.venv\Scripts\python.exe scripts\telegram_instellen.py
```

Dat zoekt je chat-id op, vraagt of het die in `.env` mag zetten, en stuurt een
testbericht.

### 2. De sleutels naar GitHub

De nachtelijke ronde draait op GitHub en kan niet bij je `.env`. De sleutels
moeten daar dus als **Secret** staan. Als `.env` helemaal is ingevuld:

```bash
.venv\Scripts\python.exe scripts\zet_github_secrets.py
```

Dat zet de drie sleutels bij de repository, met het account jort-kamphuis. De
waarden worden nergens getoond, en op GitHub kan niemand ze meer uitlezen, ook
jij niet; je kunt ze alleen vervangen.

### 3. De eerste ronde starten

Daarna draait hij elke nacht vanzelf. Wil je niet wachten, start hem dan zelf:

```bash
gh workflow run dagelijks.yml --repo jort-kamphuis/paper-trading-bot
```

Of op GitHub: tabblad **Actions**, dan **Dagelijkse ronde**, dan **Run workflow**.

### Wat er elke nacht gebeurt

1. De tests draaien. Slaagt er een niet, dan wordt er die nacht niet gehandeld
   en krijg je een melding.
2. De koersen worden opnieuw opgehaald, alleen van dagen die af zijn. Een
   aandeel dat ontbreekt, wordt nog drie keer los geprobeerd.
3. **Zijn de koersen compleet?** Ontbreekt er een symbool, mist een aandeel een
   beursdag, of halveert of verdubbelt een aandeel zonder splitsing in de data,
   dan wordt er die nacht niet gehandeld. Je krijgt een melding en de volgende
   nacht wordt de dag ingehaald. Liever een nacht overslaan dan stilletjes
   handelen op een gat of een fout.
4. De motor verwerkt dividend en splitsingen, voert de orders van gisteren uit,
   en de strategieën beslissen.
5. Was het een beursdag, dan beoordeelt de AI-analist de nieuwste dag.
6. Op zondag: het weekrapport (zie hieronder).
7. Je krijgt een kort bericht in Telegram, zonder geluid.
8. De database wordt klein gemaakt en terug in de repository gezet. Zo blijven
   de resultaten bewaard. Je ziet dat als een commit "Dagelijkse ronde".

Gaat er iets mis, dan krijg je een bericht in Telegram met een link naar het
logboek op GitHub.

### Vanaf nu lokaal alleen kijken

Zodra de meting op GitHub draait, is de database op GitHub de echte. Lokaal haal
je de laatste stand op en kijk je:

```bash
git pull
```

```bash
.venv\Scripts\python.exe scripts\stand.py --trades
```

`dagelijks.py` weigert dan lokaal te draaien. Anders zou je lokale kopie een
andere meting worden en botst het bij de volgende `git pull`. De backtest heeft
een eigen database (`data/backtest.db`) en kan dus gewoon lokaal.

---

## De proefweek (tot en met 23 november 2026)

Tot de echte meting begint, draait de bot een **proefweek**: alles wordt getest,
niets telt mee.

* **Instellingen:** `config.proefweek.yaml`, bovenop `config.yaml`. Die laatste
  verandert niet. Na de proefweek geldt vanzelf weer alleen `config.yaml`.
* **Eigen database:** `data/proefweek.db`. De echte database blijft onaangeroerd.
* **Elk Telegram-bericht** begint met `[PROEFWEEK, telt niet mee]`.
* **Vier AI-portefeuilles op alle 46 symbolen**, met precies de code van de
  echte meting:
  * `proef_ai_opus`: alles op Opus 5.5, alleen tot en met 19 november (zolang
    het gratis tegoed loopt, dat op 20 november om 00:00 UTC verloopt);
  * `proef_ai_mix`, `proef_ai_haiku` en `proef_ai_enkel`: dezelfde opzet als
    `ai_analist`, `ai_controle_haiku` en `ai_enkel` in de echte meting. Zo meet
    je vooraf wat die kosten.
* **Budget:** $90 voor de proefweek. De rondes van 20 en 21 november draaien
  zonder Opus, op de credits die je zelf bijzet.
* **De akkoordroute als test:** zondag het weekrapport (met een testvoorstel als
  Claude niets voorstelt), woensdag 18 november een extra rapport waarin de
  proef wordt beoordeeld. Dan krijg je `/akkoord` of `/afwijzen` te zien. Een
  akkoord wordt helemaal verwerkt en gemarkeerd als proefweek.
* **18 november:** een herinnering in Telegram om ongeveer $40 aan credits bij te
  zetten, met automatisch opwaarderen uit en de limiet in de console op $40.

Bekijken:

```bash
.venv\Scripts\python.exe scripts\stand.py --trades
```

```bash
.venv\Scripts\python.exe scripts\proefweek_verslag.py
```

Het verslag laat per AI-portefeuille zien welke kansen de AI gaf, hoe vaak die
de koopdrempels haalden, en wat een symbool per dag kost, met een maandschatting
voor 15, 30 en 46 symbolen. Daarmee kies je na twee proefnachten de lijst
symbolen voor de AI in de echte meting. De bot kiest dat niet zelf.

**Op 24 november schakelt de bot vanzelf om.** De ronde van 00:30 UTC die nacht
verwerkt maandag 23 november: die dag begint de echte meting (alle
portefeuilles tegelijk, met de correctie van $0,23), met de normale
instellingen, en dat meldt hij in Telegram. Wil je het eerder, dan doet dit ene
commando het (op GitHub, niet op je laptop):

```bash
.venv\Scripts\python.exe scripts\omschakelen.py
```

---

## Het weekrapport en de challenger

### Het weekrapport

Elke zondagnacht (in de ronde van 00:30 UTC) krijg je in Telegram het weekrapport.
Per strategie:

* het rendement, totaal en deze week;
* hoeveel trades er waren en welk deel winst gaf;
* de grootste daling en de Sharpe ratio;
* hoeveel van het geld gemiddeld belegd was;
* de vergelijking met de meetlatten, met een p-waarde: is het verschil al
  betekenisvol, of kan het nog geluk zijn;
* hoeveel trades er nog nodig zijn voor een harde uitspraak.

Daaronder schrijft Claude **drie conclusies** in gewone taal, op basis van alleen
die cijfers, en **hooguit één voorstel**. In de eerste weken is "het is nog te
vroeg" vaak het eerlijke antwoord, en geen voorstel ook.

Mislukt de ronde op zondag, dan komt het rapport de eerstvolgende nacht. Er wordt
nooit twee keer een rapport over dezelfde dag gemaakt (of betaald). Wil je er
een met de hand: start de workflow en kies bij **weekrapport** voor `ja`.

### De challenger

Een regel van de meting: **een lopende portefeuille wordt nooit aangepast.** Een
verbetering komt er daarom altijd als nieuwe portefeuille bij.

1. **Het voorstel.** Het mag alleen **één getal in de instellingen van één
   regelstrategie** veranderen, binnen vaste grenzen (`config.yaml`, onder
   `challenger.toegestaan`). Iets anders gooit de bot weg, en hij zegt waarom.
2. **Eerst de backtest.** Kiezen op de eerdere jaren, controleren op het laatste
   jaar: het voorstel moet het in **beide** periodes beter doen dan de huidige
   instelling. Anders start het niet.
3. **Een eerlijk paar.** Twee nieuwe portefeuilles van $1.000, op dezelfde dag:
   `..._A1` met de huidige instellingen, `..._B1` met de voorgestelde. Verder
   zijn ze gelijk.
4. **Na 4 weken** vergelijkt de bot A en B over dezelfde dagen. Alleen als B
   **betekenisvol** beter is (hoger rendement en een p-waarde van 0,05 of lager,
   met dezelfde blok-bootstrap), krijg je de vraag:

```
/akkoord 1
```

of

```
/afwijzen 1
```

   Anders stopt de proef vanzelf.
5. **Na `/akkoord`** gaat B verder als **eigen, vaste portefeuille**. Het
   origineel en `config.yaml` blijven ongewijzigd; A stopt (dat was een kopie
   van het origineel). Het staat in de tabel `wijzigingen` met de datum, en je
   krijgt een bevestiging. Zonder antwoord verandert er niets en loopt de proef
   door.

**Eerlijk over die 4 weken:** dat is statistisch weinig. Daarom de backtest
vooraf en de eis van een betekenisvol verschil.

Hooguit één proef tegelijk. De bot reageert alleen op berichten uit jouw eigen
chat, en alleen op precies deze twee opdrachten. Telegram bewaart berichten aan
een bot ongeveer een dag; antwoord je en mist de bot de nacht erna, stuur het dan
gerust opnieuw.

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

**2. Koersen ophalen.** Dit haalt 3 jaar geschiedenis op voor alle 46 symbolen:

```bash
.venv\Scripts\python.exe scripts\vernieuw_data.py --jaren 3
```

**3. De dagelijkse ronde draaien.** De eerste keer begint hiermee de meting:
elke portefeuille krijgt $1.000 nep-geld.

```bash
.venv\Scripts\python.exe scripts\dagelijks.py
```

**4. Kijken hoe het ervoor staat:**

```bash
.venv\Scripts\python.exe scripts\stand.py --trades
```

**5. De backtest draaien** (mag, maar is geen bewijs; zie verderop). De eerste
keer haalt hij zijn eigen koersen op; met `--vernieuw` doet hij dat opnieuw:

```bash
.venv\Scripts\python.exe scripts\backtest.py
```

**6. De tests draaien** (doe dit na elke wijziging):

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

### De dagelijkse ronde

`scripts/dagelijks.py` is het script dat elke nacht vanzelf draait. Het haalt de
nieuwste koersen op, verwerkt dividend en splitsingen, voert de orders van
gisteren uit, laat elke strategie beslissen en legt de stand vast. Je kunt het
zonder zorgen opnieuw draaien:

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

Alles rekent in **dollars**: de aandelen, ETF's en crypto noteren in dollars
(crypto in USDT, dat we als dollar tellen). Zo meet je de strategie, en niet de
koers van de euro. In Telegram staat het bedrag in euro's er ter informatie
achter.

Elke strategie heeft een eigen portefeuille van **$1.000 nep-geld**:

* **elke aankoop krijgt 10% van de waarde van de portefeuille** (gerekend tegen
  de slotkoersen van de beslisdag), dus hooguit **10 posities** tegelijk;
* een aankoop die nog moet worden uitgevoerd, telt al als bezette plek;
* **dividend** gaat naar de kas en telt mee in het resultaat;
* na een **splitsing** wordt het aantal stukken aangepast, zodat de waarde
  gelijk blijft.

De dagindeling is altijd dezelfde:

1. De strategie kijkt naar de koersen **tot en met vandaag** en besluit wat hij
   wil kopen of verkopen. Dat gebeurt alleen op dagen dat de Amerikaanse beurs
   open was. In het weekend handelt alleen crypto; wie dan een ranglijst maakt,
   geeft crypto een voorsprong die niets met de strategie te maken heeft.
2. De order wordt uitgevoerd op de **openingskoers van de eerstvolgende
   handelsdag** van dat symbool, met 0,05% slippage en 0,1% kosten.
3. Aan het eind van de dag wordt de waarde van de portefeuille vastgelegd.

Stap 1 en 2 staan met opzet uit elkaar. Zo kan geen enkele strategie een koers
gebruiken die hij op het moment van beslissen nog niet kon weten. Dat heet
"look-ahead" en het is de meest voorkomende manier waarop een backtest zichzelf
voor de gek houdt. `tests/test_geen_vooruitkijken.py` controleert het.

### De strategieën

Gangbare instellingen uit de literatuur, **niet** afgesteld op de backtest.

| Naam | De gedachte erachter |
|---|---|
| **Momentum** | Koop de 10 symbolen met het beste rendement over 90 kalenderdagen, gedeeld door hoe wild ze bewegen (anders wint vanzelf crypto). Verkoop zodra een naam uit de top 20 valt. |
| **Mean reversion** | De variant van Larry Connors: koop een korte, felle dip (RSI over 2 dagen onder 10), maar alleen boven het 200-daags gemiddelde. Verkoop zodra de koers boven het 5-daags gemiddelde sluit, of na 14 kalenderdagen. Hooguit 2 cryptoposities. |
| **Trend volgend** | Zolang het 50-daags gemiddelde boven het 200-daags ligt, erin; zakt het eronder, eruit. Bij te weinig plek gaan de sterkste trends voor. |
| **Meetlat SPY** | Koop op dag 1 voor het hele bedrag SPY en doe daarna niets meer. |
| **Meetlat BTC** | Hetzelfde met bitcoin. |
| **Meetlat gelijk verdeeld** | `gelijk_alle`: op dag 1 het bedrag gelijk verdeeld over alle symbolen, daarna niets meer. Volgt de AI een kleinere lijst, dan ook `gelijk_ai` over die lijst. |

De meetlatten hebben geen grens van 10 posities en geen 10%-regel: die zetten
alles in een keer in.

### Wat er gevolgd wordt

De regelstrategieën volgen **46 symbolen**: 30 grote aandelen, 8 indexfondsen
(ETF's) en 8 cryptoparen. Die lange lijst is er met een reden: hoe meer trades,
hoe sneller je kunt zien of een resultaat echt is of toeval.

Welke symbolen de AI-analist volgt (`ai_watchlist` in `config.yaml`), wordt
gekozen na twee proefnachten, op basis van de gemeten kosten, en ligt daarna
vast. Volgt de AI niet alles, dan draaien de drie regelstrategieën ook op de
AI-lijst (`momentum_ai`, enzovoort), zodat je de AI kunt vergelijken met regels
op dezelfde symbolen.

Alles staat in `config.yaml`.

### Alleen afgesloten dagen

De bot gebruikt alleen koersen van dagen die echt afgelopen zijn. Een aandelendag
is af om 17:00 in New York (een uur na sluiting), een cryptodag om middernacht
UTC. Een dag die nog bezig is, heeft als "slotkoers" gewoon de koers van dat
moment; daarop beslissen zou betekenen dat je op een halve dag handelt. Zo'n dag
wordt niet opgehaald, en als hij er toch staat, wordt hij weggehaald.

---

## De AI-analist

### Wat de AI doet, en wat niet

De AI **beslist niets**. Hij geeft per symbool, elke beursdag:

* een **kans** (0 tot 1) dat het symbool het de komende 14 kalenderdagen beter
  doet dan zijn referentie (SPY voor aandelen en ETF's, BTC voor crypto), na
  ongeveer 0,3% kosten;
* een **verwacht extra rendement** in procenten tegenover die referentie.

Wat er gekocht en verkocht wordt, beslist de **code**, met regels die voor de
start vastliggen (`config.yaml`, onder `ai.beslisregel`):

* **Kopen** bij een kans van 0,55 of meer **en** een verwacht extra rendement
  van 0,5% of meer. Eerst worden alle symbolen beoordeeld, daarna gaan de beste
  verwachtingen voor. Zo wint niet het symbool dat toevallig vooraan in de lijst
  staat.
* **Verkopen** als de kans onder 0,50 zakt, of na 14 kalenderdagen, tenzij het
  symbool dan opnieuw aan de koopregel voldoet (dan begint de houdtijd opnieuw).
* **Niet kopen** in een nacht waarin niet alles beoordeeld kon worden (budget op
  of fouten): dan zou de keuze scheef zijn.

Waarom zo? In de eerste proefronde koos de oude opzet, waarin de AI zelf
KOOP/VERKOOP/NIETS zei, alle 144 keer "niets". Zo meet je niets. Nu wordt elke
kans bewaard, ook als er niets gebeurt, en kun je achteraf nagaan of de kansen
klopten.

### Wie wat doet

De kengetallen rekent de **code** zelf uit, uit de koersen tot en met de
beslisdag: rendement over 7, 28, 90 en 180 kalenderdagen (ook tegenover de
referentie), schommeling, afstand tot het jaarhoogste, de koers tegenover het
200-daags gemiddelde, RSI, volume, de stand van de markt, en de datum van de
volgende kwartaalcijfers. Daar zit geen model tussen dat cijfers verkeerd kan
lezen.

| Rol | Doet | Model |
|---|---|---|
| Nieuwsanalist | leest de koppen en zoekt vooral naar risico's en gebeurtenissen | Claude Haiku 5.5 |
| Bull | het sterkste eerlijke argument voor | Claude Sonnet 5.5 |
| Bear | het sterkste eerlijke argument ertegen | Claude Sonnet 5.5 |
| Risk manager | weegt alles en geeft de kans en het verwachte extra rendement | Claude Sonnet 5.5 |

Bull, bear en risk manager zien ook zelf de nieuwskoppen, niet alleen de
samenvatting. Alles wordt bewaard in de tabel `ai_besluiten`: de kengetallen,
wat elke rol zei, en **welke nieuwskoppen de AI zag, met hun publicatietijd**.

### Drie AI-portefeuilles

* **`ai_analist`**: zoals hierboven. Dit is de portefeuille van de hoofdvraag.
* **`ai_controle_haiku`**: precies hetzelfde, maar alles op het goedkoopste
  model. Is het duurdere model zijn geld waard?
* **`ai_enkel`**: een enkele aanroep (Sonnet) die alles zelf afweegt, zonder
  debat. Voegt het debat iets toe?

### Regels die de bot zelf afdwingt

Een instructie aan een model is een verzoek. Deze dingen zijn regels in de code:

* De beslisregel hierboven, de 10%-regel en de grens van 10 posities.
* Een kans buiten 0 tot 1, of een antwoord dat afgekapt, geweigerd of
  onleesbaar is, telt niet. De reden komt in het logboek.
* Een geweigerde vraag wordt niet stil naar een ander model doorgestuurd. Dan
  zou je analist midden in de meting ongemerkt iemand anders worden.

### Geen nieuws uit de toekomst

De AI-analist ziet alleen koppen die vóór het moment van uitvoeren zijn
gepubliceerd (crypto: middernacht UTC, aandelen: uiterlijk de opening van de
volgende beursdag). yfinance geeft alleen de nieuwste berichten per symbool en
er is geen nieuwsgeschiedenis. Daarom:

* doet de AI-analist **niet mee in de backtest**;
* beoordeelt hij bij het inhalen van gemiste dagen **alleen de nieuwste dag**.
  Een beoordeling van drie dagen geleden met het nieuws van vandaag zou
  vooruitkijken zijn.

### Kosten

Het budget staat in `config.yaml` op **$35 per maand**; in de Anthropic-console
staat de limiet op $40 als noodrem, met automatisch opwaarderen uit.

* **Nooit twee keer betalen.** Een beoordeling die al bestaat, wordt niet opnieuw
  gevraagd. Draait het script twee keer op een dag, dan kost de tweede keer niets.
* **Elke aanroep wordt geboekt** in de tabel `ai_kosten`, met de prijzen uit
  `config.yaml`.
* **Alleen op beursdagen.** In het weekend en op feestdagen kost de AI niets.

Wat een dag echt kost:

```bash
.venv\Scripts\python.exe scripts\ai_kosten.py
```

Past het niet binnen het budget, dan stelt het script aanpassingen voor (minder
symbolen, de controleportefeuille of `ai_enkel` uit), met wat elk scheelt. Het
verandert zelf niets. Raakt het budget onverhoopt toch op, dan koopt de AI niets
nieuws meer tot de volgende maand; posities worden na hun houdtijd door de code
verkocht, en de andere strategieën lopen gewoon door.

### De sleutel

Maak een API-key aan in de Anthropic-console en zet hem in `.env`, achter
`ANTHROPIC_API_KEY=`. Dat bestand staat in `.gitignore` en gaat nooit naar
GitHub. Plak de sleutel nergens anders, ook niet in een chat. Zonder sleutel
slaat de dagronde de AI-analist over en draait de rest gewoon.

---

## Eerlijk zijn over wat dit wel en niet zegt

### Een backtest bewijst niets

De backtest kijkt achteruit met regels die we nu hebben gekozen. Dat ziet er
bijna altijd mooier uit dan wat je vooruit haalt. Hij staat hier voor andere
dingen: controleren of de motor goed rekent, zien hoeveel trades een strategie
ongeveer maakt, en de slechtste voorstellen van de challenger tegenhouden.
**De echte test begint op de startdag van de meting.**

### "Betekenisvol" of nog gewoon geluk

Twee manieren, allebei met een "bootstrap" (10.000 keer opnieuw trekken uit wat
er gebeurd is):

* **Per strategie:** het resultaat in dollars van elke afgeronde trade. Blijft
  het gemiddelde bijna altijd positief, dan is het waarschijnlijk geen toeval.
* **Tegenover een meetlat:** de dagelijkse verschillen in rendement, getrokken
  in **blokken van 10 beursdagen**. Goede dagen komen vaak achter elkaar; wie
  losse dagen trekt, lijkt zekerder dan hij is.

Daarnaast rekent de bot uit **hoeveel trades er nog nodig zijn** voor een harde
uitspraak. Dat getal valt vaak tegen: als het verschil klein is ten opzichte van
de schommeling, heb je heel veel waarnemingen nodig. Wees daar niet door
ontmoedigd, maar wees er ook niet blind voor.

### De meetlatten

SPY en BTC zetten alles in één symbool. De strategieën verdelen hun geld en
hebben soms kas over. In een stijgende markt is dat een nadeel dat niets met de
kwaliteit van hun regels te maken heeft. Daarom is er ook `gelijk_alle` (en
`gelijk_ai`): kopen en vasthouden over dezelfde symbolen waaruit de strategie
kiest. Wint een strategie daar niet van, dan voegde het kiezen niets toe.

### Vereenvoudigingen in de nep-broker

* **Fractionele stukken.** De bot koopt voor precies het bedrag dat hij wil, ook
  als dat 0,065 aandeel is. Bij een echte broker kan dat lang niet altijd.
* **USDT telt als dollar.** Die wijkt in de praktijk een fractie af; dat verschil
  negeren we.
* **Dividend zonder belasting.** Dividend gaat bruto naar de kas, op de
  ex-dividenddatum. Er wordt geen dividend- of vermogensbelasting gerekend.
* **Vaste kosten.** 0,1% per trade en 0,05% slippage, voor alles gelijk. Een
  echte broker rekent per product anders.

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
├── config.proefweek.yaml    de instellingen van de proefweek (bovenop config.yaml)
├── .env.example             voorbeeld voor je sleutels (.env gaat nooit in git)
├── requirements.txt
├── .github/workflows/
│   └── dagelijks.yml        de nachtelijke ronde op GitHub
├── data/
│   ├── trading.db           de echte database (in git, bijgewerkt door GitHub)
│   ├── proefweek.db         de proefweek (in git)
│   └── backtest.db          de backtest, met eigen koersen (niet in git)
├── bot/
│   ├── config.py            instellingen inlezen
│   ├── modus.py             proefweek of echte meting
│   ├── omschakelen.py       van de proefweek naar de echte meting
│   ├── database.py          de tabellen
│   ├── markt.py             koershistorie, zonder vooruitkijken
│   ├── indicatoren.py       RSI en gemiddeldes
│   ├── portefeuille.py      de nep-broker
│   ├── motor.py             laat alles dag na dag draaien
│   ├── statistiek.py        rendement, risico en "is dit al iets?"
│   ├── opzet.py             zet alle strategieën klaar
│   ├── overzicht.py         teksten over de stand, ook het Telegram-bericht
│   ├── tekst.py             Nederlandse notatie van getallen en datums
│   ├── telegram.py          berichten naar Telegram, en je antwoorden lezen
│   ├── volledigheid.py      zijn de koersen compleet genoeg om te handelen?
│   ├── rapport.py           de cijfers van het weekrapport
│   ├── weekrapport.py       het hele weekrapport
│   ├── challenger.py        een verbetering eerlijk testen, en je akkoord
│   ├── challenger_toets.py  de backtest die een voorstel eerst moet halen
│   ├── data/                koersen, wisselkoers, nieuws en kwartaalcijfers
│   ├── strategieen/         de strategieën en de meetlatten
│   └── ai/                  de AI-analist
│       ├── kenmerken.py     de kengetallen die de code zelf uitrekent
│       ├── rollen.py        de rollen: instructies en antwoordvorm
│       ├── analist.py       de kans per symbool (debat of enkel)
│       ├── beslisregel.py   de code die beslist wat er gekocht en verkocht wordt
│       ├── ronde.py         de dagelijkse ronde van de AI
│       ├── client.py        de verbinding met de Claude-API
│       ├── budget.py        de budgetbewaking
│       ├── kostenprognose.py  past het in een maand?
│       └── weekoordeel.py   de drie conclusies en het voorstel
├── scripts/
│   ├── vernieuw_data.py     koersen ophalen
│   ├── dagelijks.py         de dagelijkse ronde
│   ├── stand.py             laat zien hoe het ervoor staat
│   ├── ai_kosten.py         wat de AI echt kost
│   ├── proefweek_verslag.py wat de AI in de proefweek deed
│   ├── omschakelen.py       nu omschakelen naar de echte meting (via GitHub)
│   ├── backtest.py          de backtest
│   ├── herstart_meting.py   opnieuw beginnen (wist alles)
│   ├── telegram_instellen.py  chat-id vinden en testbericht sturen
│   ├── zet_github_secrets.py  sleutels uit .env naar GitHub Secrets
│   ├── slank_maken.py       database klein maken voor git (door GitHub)
│   └── meld_fout.py         foutmelding naar Telegram (door GitHub)
└── tests/                   239 tests
```

### Wanneer begint de meting

De eerste ronde van een meting onthoudt de startdatum. Die wordt gezet op de
laatste dag dat de Amerikaanse beurs open was, en niet zomaar op vandaag. Anders
zou de meting kunnen beginnen op een dag dat alleen crypto handelde, en gingen
de aandelenstrategieën en de meetlat SPY een dag later van start dan de rest. Nu
beginnen ze allemaal tegelijk.

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
* **De AI kost alleen geld op de echte database of de proefweek.** Draai je de
  ronde op een kopie (`--db`), dan slaat de bot de AI over. Anders betaal je voor
  besluiten in een bestand dat je weggooit. Wil je het toch: `--ai-op-kopie`.
* **Opnieuw beginnen wist geen kosten.** `herstart_meting.py` wist de meting,
  maar de AI-kosten van de maand blijven staan, want die zijn echt betaald. Kosten
  die buiten de bot om zijn gemaakt, boek je met `--boek-usd` en `--reden`.
* **Vaste versies.** `requirements.txt` legt de versie van elk pakket vast,
  zodat een update halverwege de meting niet ongemerkt iets verandert.
* **De Telegram-sleutel zit in elk internetadres van Telegram.** Een
  foutmelding van de verbinding noemt dat adres vaak letterlijk, en de
  logboeken van deze openbare repository zijn ook openbaar. De bot haalt de
  sleutel daarom uit elke foutmelding voordat die wordt getoond.
* **Crypto via Kraken**, met OKX en Binance als terugval. Kraken heeft alle 8
  paren en bedient ook de VS, waar de GitHub-servers staan. Welke beurs een koers
  leverde, staat per koers in de kolom `bron`.
