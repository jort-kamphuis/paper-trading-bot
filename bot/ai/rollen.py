"""De vijf rollen: wat ze te zien krijgen en wat ze terug moeten geven.

De opzet is geleend van TradingAgents: eerst twee analisten die feiten
verzamelen, dan een bull en een bear die het oneens zijn, en tot slot een risk
manager die knopen doorhakt.

Twee dingen staan vast in elke instructie:
1. Het gaat om nep-geld. Er wordt nooit echt gehandeld.
2. Als er te weinig te zeggen valt, moet de rol dat zeggen in plaats van iets
   te verzinnen. "Ik weet het niet" is een geldig antwoord en leidt tot NIETS
   DOEN, wat meestal de beste zet is.

De schema's hieronder dwingen de vorm van het antwoord af. Daardoor hoeft de
bot nooit te raden wat een rol bedoelde.
"""

from __future__ import annotations

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


SCHEMA_ANALIST = _schema(
    {
        "samenvatting": {"type": "string", "description": "Hooguit drie zinnen."},
        "signaal": {"type": "string", "enum": ["positief", "neutraal", "negatief"]},
        "zekerheid": {
            "type": "number",
            "description": "0 tot 1. Hoe zeker ben je van dit signaal?",
        },
    },
    ["samenvatting", "signaal", "zekerheid"],
)

SCHEMA_DEBAT = _schema(
    {
        "argument": {"type": "string", "description": "Hooguit vier zinnen."},
        "sterkste_punt": {"type": "string", "description": "Een zin."},
        "overtuiging": {
            "type": "number",
            "description": "0 tot 1. Hoe sterk vind je je eigen zaak?",
        },
    },
    ["argument", "sterkste_punt", "overtuiging"],
)

SCHEMA_RISK = _schema(
    {
        "actie": {"type": "string", "enum": ["KOOP", "VERKOOP", "NIETS"]},
        "onderbouwing": {
            "type": "string",
            "description": "Hooguit drie zinnen, in gewone taal, zodat een leek het snapt.",
        },
        "zekerheid": {
            "type": "number",
            "description": "0 tot 1. Onder 0,6 wordt er niet gehandeld.",
        },
        "belangrijkste_risico": {"type": "string", "description": "Een zin."},
    },
    ["actie", "onderbouwing", "zekerheid", "belangrijkste_risico"],
)


# ---------------------------------------------------------------- instructies

SYSTEEM_TECHNISCH = (
    "Je bent een technisch analist. Je krijgt kengetallen van een koers en zegt "
    "wat die laten zien. Je voorspelt niets; je beschrijft wat er staat en hoe "
    "sterk dat signaal is. Veel koersbewegingen zijn ruis, dus 'neutraal' is "
    "vaak het eerlijke antwoord.\n\n" + ONDERAAN
)

SYSTEEM_NIEUWS = (
    "Je bent een nieuwsanalist. Je krijgt nieuwskoppen over een bedrijf of fonds "
    "en beoordeelt wat ze betekenen voor de koers op korte termijn. Veel "
    "financieel nieuws is ruis: koersoverzichten, fondsen die iets kochten, "
    "algemene beschouwingen. Dat soort koppen zijn 'neutraal'. Reageer alleen "
    "op echt nieuws over het bedrijf zelf.\n\n" + ONDERAAN
)

SYSTEEM_BULL = (
    "Je bent de optimist in een debat. Je geeft het sterkste eerlijke argument "
    "om te kopen of te blijven zitten. Je mag niets verzinnen en geen zwak "
    "argument opblazen: is de zaak zwak, zeg dat dan en geef jezelf een lage "
    "overtuiging. Een eerlijke bull die toegeeft dat er weinig te halen valt, is "
    "waardevoller dan een die altijd enthousiast is.\n\n" + ONDERAAN
)

SYSTEEM_BEAR = (
    "Je bent de pessimist in een debat. Je geeft het sterkste eerlijke argument "
    "om niet te kopen of juist te verkopen. Je mag niets verzinnen en geen zwak "
    "argument opblazen: is de zaak zwak, zeg dat dan en geef jezelf een lage "
    "overtuiging. Een eerlijke bear die toegeeft dat er weinig mis is, is "
    "waardevoller dan een die altijd somber is.\n\n" + ONDERAAN
)

SYSTEEM_RISK = (
    "Je bent de risk manager en jij hakt de knoop door. Je leest beide analisten "
    "en beide kanten van het debat, en kiest: KOOP, VERKOOP of NIETS.\n\n"
    "Je regels:\n"
    "* NIETS DOEN is de standaard. Handel alleen als er een duidelijke reden is.\n"
    "* Twijfel je, geef dan een lage zekerheid. Onder 0,6 wordt er niet gehandeld, "
    "en dat is prima.\n"
    "* Elke trade kost geld aan kosten en slippage. Een klein voordeel is het niet "
    "waard.\n"
    "* Je kunt alleen KOOP kiezen als de positie er nog niet is, en alleen VERKOOP "
    "als die er wel is. Dat staat erbij.\n"
    "* Schrijf de onderbouwing zo dat iemand zonder handelservaring hem begrijpt.\n\n"
    + ONDERAAN
)


# ------------------------------------------------------------------ berichten


def bericht_technisch(symbool: str, datum: str, kengetallen: dict) -> str:
    regels = [f"Symbool: {symbool}", f"Beslisdag: {datum}", "", "Kengetallen:"]
    for naam, waarde in kengetallen.items():
        regels.append(f"  {naam}: {waarde}")
    regels.append("")
    regels.append(
        "Wat laten deze kengetallen zien over de richting op korte termijn?"
    )
    return "\n".join(regels)


def bericht_nieuws(symbool: str, datum: str, koppen: list[dict]) -> str:
    regels = [f"Symbool: {symbool}", f"Beslisdag: {datum}", ""]
    if not koppen:
        regels.append("Er zijn geen nieuwskoppen gevonden voor dit symbool.")
        regels.append("Geef dan signaal 'neutraal' en zekerheid 0.")
        return "\n".join(regels)

    regels.append(f"Nieuwskoppen ({len(koppen)}), nieuwste eerst:")
    for kop in koppen:
        regels.append(f"\n  [{kop['gepubliceerd']}] {kop['titel']}")
        if kop.get("bron"):
            regels.append(f"    bron: {kop['bron']}")
        samenvatting = (kop.get("samenvatting") or "")[:400]
        if samenvatting:
            regels.append(f"    {samenvatting}")
    regels.append("")
    regels.append("Wat betekent dit nieuws voor de koers op korte termijn?")
    return "\n".join(regels)


def bericht_debat(
    symbool: str, datum: str, technisch: dict, nieuws: dict, heeft_positie: bool
) -> str:
    return "\n".join(
        [
            f"Symbool: {symbool}",
            f"Beslisdag: {datum}",
            f"Positie in portefeuille: {'ja' if heeft_positie else 'nee'}",
            "",
            "De technisch analist zegt:",
            f"  signaal: {technisch.get('signaal', 'onbekend')} "
            f"(zekerheid {technisch.get('zekerheid', 0)})",
            f"  {technisch.get('samenvatting', '')}",
            "",
            "De nieuwsanalist zegt:",
            f"  signaal: {nieuws.get('signaal', 'onbekend')} "
            f"(zekerheid {nieuws.get('zekerheid', 0)})",
            f"  {nieuws.get('samenvatting', '')}",
            "",
            "Geef je argument.",
        ]
    )


def bericht_risk(
    symbool: str,
    datum: str,
    technisch: dict,
    nieuws: dict,
    bull: dict,
    bear: dict,
    heeft_positie: bool,
    kas_eur: float,
    max_per_trade_eur: float,
) -> str:
    toegestaan = []
    if heeft_positie:
        toegestaan.append("VERKOOP")
    elif kas_eur >= max_per_trade_eur:
        toegestaan.append("KOOP")
    toegestaan.append("NIETS")

    return "\n".join(
        [
            f"Symbool: {symbool}",
            f"Beslisdag: {datum}",
            f"Positie in portefeuille: {'ja' if heeft_positie else 'nee'}",
            f"Kas: {kas_eur:.2f} euro, inleg per trade: {max_per_trade_eur:.2f} euro",
            f"Toegestane acties nu: {', '.join(toegestaan)}",
            "",
            f"Technisch analist: {technisch.get('signaal', 'onbekend')} "
            f"(zekerheid {technisch.get('zekerheid', 0)})",
            f"  {technisch.get('samenvatting', '')}",
            "",
            f"Nieuwsanalist: {nieuws.get('signaal', 'onbekend')} "
            f"(zekerheid {nieuws.get('zekerheid', 0)})",
            f"  {nieuws.get('samenvatting', '')}",
            "",
            f"Bull (overtuiging {bull.get('overtuiging', 0)}):",
            f"  {bull.get('argument', '')}",
            f"  sterkste punt: {bull.get('sterkste_punt', '')}",
            "",
            f"Bear (overtuiging {bear.get('overtuiging', 0)}):",
            f"  {bear.get('argument', '')}",
            f"  sterkste punt: {bear.get('sterkste_punt', '')}",
            "",
            "Wat doen we?",
        ]
    )
