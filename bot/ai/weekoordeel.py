"""Claude leest het weekrapport en schrijft drie conclusies en hooguit een voorstel.

Het voorstel wordt daarna door de bot zelf getoetst (bot/challenger.py). Een
voorstel buiten de toegestane instellingen of grenzen wordt weggegooid, wat
Claude ook schrijft.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.ai.client import ClaudeClient
from bot.challenger import Voorstel

SYSTEEM = (
    "Je bent een nuchtere, onafhankelijke onderzoeker. Je leest het weekrapport van "
    "een experiment met nep-geld: een paar handelsstrategieën worden vergeleken met "
    "simpelweg de markt kopen en vasthouden. Er wordt nooit echt gehandeld en dit is "
    "geen beleggingsadvies.\n\n"
    "Je schrijft precies drie conclusies in gewone taal, voor iemand zonder "
    "handelservaring. Elke conclusie is een of twee zinnen.\n\n"
    "Regels:\n"
    "* Gebruik alleen de cijfers uit het rapport. Verzin geen cijfers, gebeurtenissen "
    "of verklaringen die niet uit de cijfers volgen.\n"
    "* Wees eerlijk over toeval. Met weinig trades of een hoge p-waarde is het "
    "eerlijke antwoord vaak: het is nog te vroeg om iets te zeggen. Zeg dat dan.\n"
    "* Vergelijk met de meetlatten, en houd er rekening mee dat de strategieën maar een "
    "deel van hun geld belegd hebben.\n\n"
    "Daarnaast mag je hooguit een verbetering voorstellen: een instelling van een "
    "regelstrategie aanpassen, alleen uit de lijst met toegestane instellingen en "
    "binnen de grenzen. Die wordt dan vier weken eerlijk getest naast de huidige "
    "versie. Stel alleen iets voor als je er een concrete reden voor ziet in de "
    "cijfers. Geen voorstel is vaak het beste antwoord, zeker in de eerste weken: "
    "een aanpassing op basis van toeval maakt het experiment slechter. Loopt er al "
    "een proef, stel dan niets voor."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "conclusies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Precies drie conclusies, elk een of twee zinnen.",
        },
        "heeft_voorstel": {"type": "boolean"},
        "voorstel_strategie": {"type": "string", "description": "Leeg als er geen voorstel is."},
        "voorstel_parameter": {"type": "string", "description": "Leeg als er geen voorstel is."},
        "voorstel_waarde": {"type": "number", "description": "0 als er geen voorstel is."},
        "voorstel_onderbouwing": {
            "type": "string",
            "description": "Twee zinnen: wat je in de cijfers ziet en waarom dit zou helpen. Leeg als er geen voorstel is.",
        },
    },
    "required": [
        "conclusies", "heeft_voorstel", "voorstel_strategie",
        "voorstel_parameter", "voorstel_waarde", "voorstel_onderbouwing",
    ],
    "additionalProperties": False,
}


@dataclass
class Weekoordeel:
    conclusies: list[str]
    voorstel: Voorstel | None
    kosten_usd: float
    probleem: str | None = None


def _instellingen_tekst(cfg: dict) -> str:
    regels = ["Huidige instellingen van de regelstrategieën:"]
    for naam, instellingen in cfg["strategieen"].items():
        if naam == "benchmarks":
            continue
        delen = [f"{k}={v}" for k, v in instellingen.items() if k != "actief"]
        regels.append(f"  {naam}: " + ", ".join(delen))
    regels.append("")
    regels.append("Toegestane aanpassingen (instelling: minimum tot maximum):")
    for naam, params in cfg["challenger"]["toegestaan"].items():
        for param, g in params.items():
            soort = " (heel getal)" if g.get("geheel") else ""
            regels.append(f"  {naam}.{param}: {g['min']} tot {g['max']}{soort}")
    return "\n".join(regels)


def vraag_oordeel(
    client: ClaudeClient, cfg: dict, cijfers_tekst: str, proef_tekst: str
) -> Weekoordeel:
    c = cfg["challenger"]
    bericht = "\n\n".join([cijfers_tekst, proef_tekst, _instellingen_tekst(cfg)])
    antwoord = client.vraag(
        rol="rapport",
        model=c["model"],
        systeem=SYSTEEM,
        bericht=bericht,
        max_tokens=int(c["max_tokens"]),
        schema=SCHEMA,
        denken="aan",
        inspanning=c["inspanning"],
    )
    if antwoord.probleem():
        return Weekoordeel([], None, antwoord.usd, probleem=antwoord.probleem())

    d = antwoord.data
    conclusies = [str(x).strip() for x in d.get("conclusies", []) if str(x).strip()][:3]
    voorstel = None
    if d.get("heeft_voorstel"):
        voorstel = Voorstel(
            strategie=str(d.get("voorstel_strategie", "")).strip(),
            parameter=str(d.get("voorstel_parameter", "")).strip(),
            nieuwe_waarde=float(d.get("voorstel_waarde", 0) or 0),
            onderbouwing=str(d.get("voorstel_onderbouwing", "")).strip(),
        )
    return Weekoordeel(conclusies, voorstel, antwoord.usd)
