"""De rollen van de AI-analist: wat ze te zien krijgen en wat ze teruggeven.

Opzet (naar het idee van TradingAgents, aangepast na de review):
* De kengetallen rekent de code zelf uit (bot/ai/kenmerken.py).
* De nieuwsanalist kijkt naar risico's en gebeurtenissen die eraan komen.
* Bull en bear krijgen de tabel, de ruwe koppen en de nieuwssamenvatting.
* De risk manager geeft een KANS en een VERWACHT EXTRA RENDEMENT. Hij beslist
  niet; dat doet de code met vaste regels (config.yaml, ai.beslisregel).
* ai_enkel doet alles in een aanroep: dezelfde gegevens, dezelfde uitkomst.

De kans gaat altijd over hetzelfde: doet dit symbool de komende 14
kalenderdagen beter dan zijn referentie (SPY voor aandelen en indexfondsen,
BTC voor crypto), na ongeveer 0,3% kosten voor kopen en verkopen samen?
Deze kans wordt voor elk symbool elke beursdag opgeslagen en later gescoord.
"""

from __future__ import annotations

from typing import Any

from bot.ai.kenmerken import als_tekst

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

SCHEMA_KANS = _schema(
    {
        "kans": {
            "type": "number",
            "description": (
                "0 tot 1: de kans dat dit symbool over de komende 14 kalenderdagen, na 0,3% "
                "kosten, beter doet dan de referentie. 0,5 betekent: geen idee."
            ),
        },
        "verwacht_extra_rendement_pct": {
            "type": "number",
            "description": (
                "Het verwachte rendement van dit symbool min dat van de referentie over 14 "
                "kalenderdagen, in procentpunten, voor kosten. Kan negatief zijn."
            ),
        },
        "onderbouwing": {
            "type": "string",
            "description": "Hooguit drie zinnen, in gewone taal, zodat een leek het snapt.",
        },
        "belangrijkste_risico": {"type": "string", "description": "Een zin."},
    },
    ["kans", "verwacht_extra_rendement_pct", "onderbouwing", "belangrijkste_risico"],
)


# ---------------------------------------------------------------- instructies

SYSTEEM_NIEUWS = (
    "Je bent een nieuwsanalist. Je krijgt de recente nieuwskoppen over een bedrijf, "
    "fonds of cryptomunt. Je zoekt vooral naar RISICO'S en GEBEURTENISSEN DIE ERAAN "
    "KOMEN: kwartaalcijfers, rechtszaken, toezichthouders, productlanceringen, "
    "overnames. Je probeert niet te voorspellen of de koers stijgt; nieuws dat al in "
    "de koppen staat, zit meestal al in de koers. Veel financieel nieuws is ruis "
    "(koersoverzichten, fondsen die iets kochten, algemene beschouwingen); noem dat "
    "dan ook zo.\n\n" + ONDERAAN
)

SYSTEEM_BULL = (
    "Je bent de optimist in een debat over de komende 14 dagen. Je geeft het "
    "sterkste eerlijke argument dat dit symbool beter gaat doen dan zijn "
    "referentie. Je mag niets verzinnen en geen zwak argument opblazen: is de zaak "
    "zwak, zeg dat dan en geef jezelf een lage overtuiging.\n\n" + ONDERAAN
)

SYSTEEM_BEAR = (
    "Je bent de pessimist in een debat over de komende 14 dagen. Je geeft het "
    "sterkste eerlijke argument dat dit symbool slechter gaat doen dan zijn "
    "referentie. Je mag niets verzinnen en geen zwak argument opblazen: is de zaak "
    "zwak, zeg dat dan en geef jezelf een lage overtuiging.\n\n" + ONDERAAN
)

_KANS_REGELS = (
    "Je beslist NIET of er gekocht of verkocht wordt. Dat doet de code met vaste "
    "regels. Jij geeft een eerlijke kans en een verwacht extra rendement.\n\n"
    "Wees gekalibreerd. Als je honderd keer 0,6 zegt, moet het ongeveer zestig keer "
    "kloppen. Over 14 dagen beweegt een koers voor een groot deel door toeval; een "
    "kans ver van 0,5 hoort bij een duidelijke reden in de gegevens. Je wordt achteraf "
    "gescoord op hoe goed je kansen klopten, niet op hoe vaak je iets durfde."
)

SYSTEEM_RISK = (
    "Je bent de risk manager. Je leest de kengetallen, het nieuws en beide kanten van "
    "het debat, en schat in hoe dit symbool het de komende 14 kalenderdagen doet "
    "tegenover zijn referentie.\n\n" + _KANS_REGELS + "\n\n" + ONDERAAN
)

SYSTEEM_ENKEL = (
    "Je bent een nuchtere analist. Je leest de kengetallen en de nieuwskoppen, weegt "
    "zelf de argumenten voor en tegen, en schat in hoe dit symbool het de komende 14 "
    "kalenderdagen doet tegenover zijn referentie.\n\n" + _KANS_REGELS + "\n\n" + ONDERAAN
)


# ------------------------------------------------------------------ berichten


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
            _positie_tekst(positie),
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
            _positie_tekst(positie),
            _portefeuille_tekst(portefeuille),
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
            "",
            "Kengetallen (uitgerekend door de code):",
            als_tekst(kenmerken),
            "",
            _koppen_tekst(koppen),
            "",
            _positie_tekst(positie),
            _portefeuille_tekst(portefeuille),
            "",
            "Weeg de argumenten voor en tegen, en geef je kans en je verwachting.",
        ]
    )
