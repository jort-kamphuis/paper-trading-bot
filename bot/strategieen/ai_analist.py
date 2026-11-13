"""De AI-analist als strategie in de motor.

Deze strategie beslist in de motor zelf niets. Het nadenken gebeurt apart, na
de gewone dagronde, in bot/ai/ronde.py. Die zet zijn orders rechtstreeks in de
wachtrij, en de motor voert ze de volgende handelsdag uit, net als bij elke
andere strategie: zelfde kosten, zelfde slippage, zelfde 10%-regel.

Waarom apart? Elke vraag aan de API kost geld en dat geld is uitgegeven, ook
als de dagronde daarna mislukt. Daarom worden de beoordelingen en de kosten
meteen en los vastgelegd.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class AIStrategie(Strategie):
    def __init__(self, naam: str, model_override: str | None = None, soort: str = "debat"):
        super().__init__({})
        self.naam = naam
        self.soort_ai = soort  # debat of enkel
        self.model_override = model_override

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        return []
