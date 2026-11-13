"""De rollen van de AI-analist: wat ze te zien krijgen en wat ze teruggeven.

Opzet (naar het idee van TradingAgents, aangepast na de review):
* De kengetallen rekent de code zelf uit (bot/ai/kenmerken.py).
* De nieuwsanalist kijkt naar risico's en gebeurtenissen die eraan komen.
* Bull en bear krijgen de tabel, de ruwe koppen en de nieuwssamenvatting.
* De risk manager geeft een KANS en een VERWACHT EXTRA RENDEMENT. Hij beslist
  niet; dat doet de code met vaste regels (config.yaml, ai.beslisregel).
* ai_enkel doet alles in een aanroep: dezelfde gegevens, dezelfde uitkomst.

De kans gaat over een van twee vragen, die in elk bericht staat uitgeschreven:
* relatief (bijna alle symbolen): doet dit symbool de komende 14 kalenderdagen
  beter dan zijn referentie (SPY voor aandelen en indexfondsen, BTC voor
  crypto), na ongeveer 0,3% kosten voor kopen en verkopen samen?
* absoluut (SPY en BTC zelf, die de referentie zijn): stijgt het de komende 14
  kalenderdagen meer dan die 0,3% kosten? De referentie is dan kas.
Deze kans wordt voor elk symbool elke beursdag opgeslagen, met de soort vraag,
en later per soort apart gescoord (bot/ai/kalibratie.py).

De vraag gaat alleen over het symbool, niet over de eigen portefeuille: geen
eigen positie, resultaat of bezette plekken. Dan krijgt elk symbool in elke
AI-portefeuille precies dezelfde vraag, en hangt de kans niet af van wat de
portefeuille toevallig al heeft (review november 2026). De code beslist toch.
"""

from __future__ import annotations

from typing import Any

from bot.ai.kenmerken import ABSOLUUT, als_tekst

ONDERAAN = (
    "Dit is een oefening met nep-geld. Er wordt nooit echt gehandeld en dit is "
    "geen beleggingsadvies. Schrijf in het Nederlands, kort en zonder opsmuk. "
    "Baseer je uitsluitend op de gegevens hieronder. Weet je iets niet, zeg dat "
    "dan; verzin nooit cijfers, gebeurtenissen of bronnen."
)


def _schema(eigenschappen: dict, verplicht: list[str]) -> dict:
    return {
        "type": "object",
        "properties": eigenschappen,
        "required": verplicht,
        "additionalProperties": False,
    }


SCHEMA_NIEUWS = _schema(
    {
        "samenvatting": {"type": "string", "description": "Hooguit drie zinnen."},
        "belangrijkste_risico": {"type": "string", "description": "Een zin, of 'geen'."},
        "gebeurtenis_binnenkort": {
            "type": "boolean",
            "description": "Komt er in de komende 14 dagen iets dat de koers flink kan bewegen?",
        },
    },
    ["samenvatting", "belangrijkste_risico", "gebeurtenis_binnenkort"],
)

SCHEMA_DEBAT = _schema(
    {
        "argument": {"type": "string", "description": "Hooguit vier zinnen."},
        "sterkste_punt": {"type": "string", "description": "Een zin."},
        "overtuiging": {"type": "number", "description": "0 tot 1. Hoe sterk vind je je eigen zaak?"},
    },
    ["argument", "sterkste_punt", "overtuiging"],
)

# Eerst de onderbouwing, dan de getallen: zo volgt de kans uit de afweging en
# wordt de afweging niet achteraf bij een getal geschreven.
SCHEMA_KANS = _schema(
    {
        "onderbouwing": {
            "type": "string",
            "description": "Hooguit drie zinnen, in gewone taal, zodat een leek het snapt.",
        },
        "belangrijkste_risico": {"type": "string", "description": "Een zin."},
        "kans": {
            "type": "number",
            "description": (
                "0 tot 1: de kans dat het antwoord op de vraag in het bericht ja is, over de "
                "periode die daar staat en na 0,3% kosten. Zonder bijzondere reden: de "
                "basiskans uit het bericht."
            ),
        },
        "verwacht_extra_rendement_pct": {
            "type": "number",
            "description": (
                "Het verwachte rendement van dit symbool min dat van de referentie over "
                "dezelfde periode, in procentpunten, voor kosten (bij referentie kas: het "
                "verwachte rendement zelf). Kan negatief zijn."
            ),
        },
    },
    ["onderbouwing", "belangrijkste_risico", "kans", "verwacht_extra_rendement_pct"],
)


# ---------------------------------------------------------------- instructies

SYSTEEM_NIEUWS = (
    "Je bent een nieuwsanalist. Je krijgt de recente nieuwskoppen over een bedrijf, "
    "fonds of cryptomunt. Je zoekt vooral naar RISICO'S en GEBEURTENISSEN DIE ERAAN "
    "KOMEN: kwartaalcijfers, rechtszaken, toezichthouders, productlanceringen, "
    "overnames. Je probeert niet te voorspellen of de koers stijgt; nieuws dat al in "
    "de koppen staat, zit meestal al in de koers. Veel financieel nieuws is ruis "
    "(koersoverzichten, fondsen die iets kochten, algemene beschouwingen); noem dat "
    "dan ook zo. Tel alleen wat over het symbool zelf gaat: koppen over andere "
    "bedrijven of over de markt in het algemeen zeggen weinig over dit symbool.\n\n" + ONDERAAN
)

SYSTEEM_BULL = (
    "Je bent de optimist in een debat over de komende 14 dagen. Je geeft het "
    "sterkste eerlijke argument dat het antwoord op de vraag in het bericht ja is "
    "(beter dan de referentie, of bij kas: meer stijgen dan de kosten). Je mag niets verzinnen en geen zwak argument opblazen: is de zaak "
    "zwak, zeg dat dan en geef jezelf een lage overtuiging.\n\n" + ONDERAAN
)

SYSTEEM_BEAR = (
    "Je bent de pessimist in een debat over de komende 14 dagen. Je geeft het "
    "sterkste eerlijke argument dat het antwoord op de vraag in het bericht nee is "
    "(niet beter dan de referentie, of bij kas: niet meer stijgen dan de kosten). Je mag niets verzinnen en geen zwak argument opblazen: is de zaak "
    "zwak, zeg dat dan en geef jezelf een lage overtuiging.\n\n" + ONDERAAN
)

_KANS_REGELS = (
    "Je beslist NIET of er gekocht of verkocht wordt. Dat doet de code met vaste "
    "regels. Jij geeft een eerlijke kans en een verwacht extra rendement.\n\n"
    "Wees gekalibreerd. Als je honderd keer 0,6 zegt, moet het ongeveer zestig keer "
    "kloppen. Over 14 dagen beweegt een koers voor een groot deel door toeval. In het "
    "bericht staat de basiskans: hoe vaak het antwoord op deze vraag de afgelopen twee "
    "jaar ja was voor dit soort symbool. Die is niet 0,5. Weet je niets bijzonders, geef "
    "dan die basiskans. Wijk er alleen van af met een duidelijke reden in de gegevens, "
    "naar boven en naar beneden even gemakkelijk. Je wordt achteraf gescoord op hoe "
    "goed je kansen klopten, niet op hoe vaak je iets durfde."
)

SYSTEEM_RISK = (
    "Je bent de voorspeller van het team. Je leest de kengetallen, het nieuws en beide "
    "kanten van het debat, en schat in hoe dit symbool het doet tegenover zijn "
    "referentie. De vraag, met de precieze periode, staat in het bericht.\n\n" + _KANS_REGELS + "\n\n" + ONDERAAN
)

SYSTEEM_ENKEL = (
    "Je bent een nuchtere analist. Je leest de kengetallen en de nieuwskoppen, weegt "
    "zelf de argumenten voor en tegen, en schat in hoe dit symbool het doet tegenover "
    "zijn referentie. De vraag, met de precieze periode, staat in het bericht.\n\n" + _KANS_REGELS + "\n\n" + ONDERAAN
)


# ------------------------------------------------------------------ berichten


def vraag_tekst(symbool: str, kenmerken: dict) -> str:
    """De vraag waar de kans over gaat, uitgeschreven."""
    ref = kenmerken["referentie"]
    # Zo wordt er ook gekocht en gescoord (bot/ai/kalibratie.py). Zonder deze zin kan
    # het model denken dat het een sprong na het slot van de beslisdag nog meepakt.
    periode = (
        "gemeten van de openingskoers van de eerstvolgende handelsdag na de beslisdag tot de "
        "slotkoers 14 kalenderdagen na de beslisdag"
    )
    nabeurs = (
        "Nieuws van na het slot van de beslisdag zit bij die opening al in de koers; daar "
        "valt niets meer aan te verdienen."
    )
    basis = kenmerken.get("basiskans")
    basiskans = (
        f" Basiskans: de afgelopen twee jaar was het antwoord op deze vraag in ongeveer "
        f"{round(100 * basis)}% van de gevallen ja voor dit soort symbool."
        if basis is not None else ""
    )
    if kenmerken.get("vraag") == ABSOLUUT:
        return (
            f"De vraag: stijgt {symbool} meer dan ongeveer 0,3% kosten, {periode}? {symbool} is "
            f"zelf de meetlat van de markt; vergelijken met zichzelf zegt niets, dus de referentie "
            f"is kas (geld dat niets doet). {nabeurs} Het verwachte extra rendement is hier het "
            f"verwachte rendement van {symbool} zelf over die periode.{basiskans}"
        )
    return (
        f"De vraag: doet {symbool} het beter dan {ref}, na ongeveer 0,3% kosten, {periode}? "
        f"{nabeurs} Het verwachte extra rendement is het rendement van {symbool} min dat van "
        f"{ref} over die periode.{basiskans}"
    )


def _koppen_tekst(koppen: list[dict]) -> str:
    if not koppen:
        return "Nieuwskoppen: geen gevonden."
    regels = [f"Nieuwskoppen ({len(koppen)}), nieuwste eerst:"]
    for kop in koppen:
        regels.append(f"  [{kop['gepubliceerd']}] {kop['titel']}")
        samenvatting = (kop.get("samenvatting") or "")[:300]
        if samenvatting:
            regels.append(f"    {samenvatting}")
    return "\n".join(regels)


def _positie_tekst(positie: dict[str, Any] | None) -> str:
    if not positie:
        return "Positie: geen."
    return "\n".join(
        [
            "Positie: ja.",
            f"  gekocht op {positie['gekocht_op']} voor {positie['koopprijs']:.4f} dollar per stuk",
            f"  resultaat tot nu toe: {positie['resultaat_pct']:+.1f}%",
            f"  dagen in bezit: {positie['dagen']}",
            f"  reden van de aankoop: {positie['koopreden'] or '-'}",
        ]
    )


def _portefeuille_tekst(p: dict[str, Any]) -> str:
    return (
        f"Portefeuille: {p['posities']} van de {p['max_posities']} plekken bezet, "
        f"{p['belegd_pct']:.0f}% belegd, {p['crypto_pct']:.0f}% in crypto."
    )


def bericht_nieuws(symbool: str, datum: str, koppen: list[dict]) -> str:
    return "\n".join([f"Symbool: {symbool}", f"Beslisdag: {datum}", "", _koppen_tekst(koppen)])


def bericht_debat(
    symbool: str, datum: str, kenmerken: dict, koppen: list[dict], nieuws: dict,
    positie: dict | None,
) -> str:
    return "\n".join(
        [
            f"Symbool: {symbool}  (referentie: {kenmerken['referentie']})",
            f"Beslisdag: {datum}",
            vraag_tekst(symbool, kenmerken),
            "",
            "Kengetallen (uitgerekend door de code):",
            als_tekst(kenmerken),
            "",
            _koppen_tekst(koppen),
            "",
            "De nieuwsanalist zegt:",
            f"  {nieuws.get('samenvatting', '')}",
            f"  belangrijkste risico: {nieuws.get('belangrijkste_risico', '')}",
            f"  gebeurtenis binnenkort: {'ja' if nieuws.get('gebeurtenis_binnenkort') else 'nee'}",
            "",
            "Geef je argument.",
        ]
    )


def bericht_risk(
    symbool: str, datum: str, kenmerken: dict, koppen: list[dict], nieuws: dict,
    bull: dict, bear: dict, positie: dict | None, portefeuille: dict,
) -> str:
    return "\n".join(
        [
            f"Symbool: {symbool}  (referentie: {kenmerken['referentie']})",
            f"Beslisdag: {datum}",
            vraag_tekst(symbool, kenmerken),
            "",
            "Kengetallen (uitgerekend door de code):",
            als_tekst(kenmerken),
            "",
            _koppen_tekst(koppen),
            "",
            "Nieuwsanalist:",
            f"  {nieuws.get('samenvatting', '')}",
            f"  belangrijkste risico: {nieuws.get('belangrijkste_risico', '')}",
            "",
            f"Bull (overtuiging {bull.get('overtuiging', 0)}): {bull.get('argument', '')}",
            f"  sterkste punt: {bull.get('sterkste_punt', '')}",
            f"Bear (overtuiging {bear.get('overtuiging', 0)}): {bear.get('argument', '')}",
            f"  sterkste punt: {bear.get('sterkste_punt', '')}",
            "",
            "Geef je kans en je verwachting.",
        ]
    )


def bericht_enkel(
    symbool: str, datum: str, kenmerken: dict, koppen: list[dict],
    positie: dict | None, portefeuille: dict,
) -> str:
    return "\n".join(
        [
            f"Symbool: {symbool}  (referentie: {kenmerken['referentie']})",
            f"Beslisdag: {datum}",
            vraag_tekst(symbool, kenmerken),
            "",
            "Kengetallen (uitgerekend door de code):",
            als_tekst(kenmerken),
            "",
            _koppen_tekst(koppen),
            "",
            "Weeg de argumenten voor en tegen, en geef je kans en je verwachting.",
        ]
    )
