"""De AI-analist als strategie in de motor.

Deze strategie beslist in de motor zelf niets. Het nadenken gebeurt apart,
na de gewone dagronde, in bot/ai/ronde.py. Die zet zijn orders rechtstreeks in
de wachtrij, en de motor voert ze de volgende handelsdag uit, net als bij elke
andere strategie: zelfde kosten, zelfde slippage, zelfde grens per trade.

Waarom apart? Elke vraag aan de API kost geld en dat geld is uitgegeven, ook
als de dagronde daarna mislukt en wordt teruggedraaid. Daarom worden de
beslissingen en de kosten meteen en los vastgelegd. En een beslissing die al
bestaat, wordt niet opnieuw gevraagd: twee keer draaien kost geen extra geld.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class AIStrategie(Strategie):
    lijst = "ai"

    def __init__(self, naam: str, model_override: str | None = None):
        super().__init__({})
        self.naam = naam
        # Gevuld bij de controleportefeuille: alle rollen op dit ene model.
        self.model_override = model_override

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        return []
