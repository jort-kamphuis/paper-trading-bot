"""De budgetbewaking.

De Claude-API vertelt na elke aanroep hoeveel tokens er zijn gebruikt, niet
wat het kostte. Die som maken we hier, met de prijzen uit config.yaml.

De bewaking werkt met twee sloten:
1. Dit slot. De bot telt zijn eigen uitgaven van deze maand op en stopt netjes
   als het bedrag uit config.yaml bereikt is. Hij meldt dat en de andere
   strategieën lopen gewoon door.
2. Het tegoed in de Anthropic-console: in een keer gekocht voor de hele meting,
   met automatisch opwaarderen uit. Is dat op, dan herkent de bot de fout en
   meldt hij "tegoed op" in Telegram.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date


class BudgetOp(Exception):
    """Wordt opgeworpen als het maandbudget bereikt is."""


@dataclass
class Kosten:
    model: str
    tokens_in: int
    tokens_uit: int
    usd: float


def huidige_maand(vandaag: str | None = None) -> str:
    """De maand als JJJJ-MM."""
    return (vandaag or date.today().isoformat())[:7]


def bereken_kosten(prijzen: dict, model: str, tokens_in: int, tokens_uit: int) -> Kosten:
    """Reken tokens om naar dollars.

    Staat het model niet in de prijslijst, dan rekenen we met de duurste prijs
    die we kennen. Liever te voorzichtig dan een budget dat stilletjes overloopt.
    """
    prijs = prijzen.get(model)
    if prijs is None:
        duurste = max(prijzen.values(), key=lambda p: p["uitvoer"]) if prijzen else None
        if duurste is None:
            raise ValueError("geen prijzen in config.yaml")
        prijs = duurste
    usd = (tokens_in / 1_000_000.0) * float(prijs["invoer"]) + (
        tokens_uit / 1_000_000.0
    ) * float(prijs["uitvoer"])
    return Kosten(model=model, tokens_in=tokens_in, tokens_uit=tokens_uit, usd=usd)


class Budget:
    """Houdt bij wat er deze maand is uitgegeven en wanneer het op is."""

    def __init__(self, con: sqlite3.Connection, cfg: dict, vandaag: str | None = None):
        self.con = con
        self.prijzen = cfg["prijzen"]
        instellingen = cfg["ai"]["budget"]
        self.max_usd = float(instellingen["max_usd_per_maand"])
        self.waarschuw_onder = float(instellingen["waarschuw_onder_usd"])
        self.maand = huidige_maand(vandaag)
        self.vandaag = vandaag or date.today().isoformat()

    # ---------- stand ----------

    def uitgegeven(self) -> float:
        rij = self.con.execute(
            "SELECT COALESCE(SUM(kosten_usd), 0.0) AS totaal FROM ai_kosten WHERE maand = ?",
            (self.maand,),
        ).fetchone()
        return float(rij["totaal"])

    def resterend(self) -> float:
        return max(0.0, self.max_usd - self.uitgegeven())

    def is_op(self) -> bool:
        return self.uitgegeven() >= self.max_usd

    def waarschuwt(self) -> bool:
        return not self.is_op() and self.resterend() <= self.waarschuw_onder

    # ---------- gebruiken ----------

    def controleer(self) -> None:
        """Werp een fout op als er geen ruimte meer is. Roep dit aan voor elke aanroep."""
        if self.is_op():
            raise BudgetOp(
                f"Het AI-budget van ${self.max_usd:.2f} voor {self.maand} is op "
                f"(uitgegeven: ${self.uitgegeven():.2f}). De AI-analist stopt tot "
                f"volgende maand. De andere strategieën lopen gewoon door."
            )

    def boek(self, rol: str, kosten: Kosten, model_antwoord: str | None = None,
             verzoek_id: str | None = None) -> None:
        """Leg de kosten van een aanroep vast, met het model dat de API terugmeldde."""
        self.con.execute(
            """INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd,
                                       model_antwoord, verzoek_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self.vandaag,
                self.maand,
                rol,
                kosten.model,
                kosten.tokens_in,
                kosten.tokens_uit,
                kosten.usd,
                model_antwoord,
                verzoek_id,
            ),
        )

    # ---------- reserve voor de hoofdvraag ----------

    def reserve_voor(self, hoofd: str, datum: str, cfg: dict) -> float:
        """Wat de portefeuille van de hoofdvraag deze maand nog nodig heeft.

        Zijn gemiddelde kosten per nacht, maal de AI-nachten die deze maand nog komen.
        Een bijportefeuille (het Haiku-potje, ai_enkel) mag alleen draaien als er
        daarna nog zoveel over is: raakt het budget krap, dan stoppen die eerst en
        maakt de hoofdvraag de maand af.
        """
        from datetime import date, timedelta

        from bot.volledigheid import beurs_open_volgens_kalender

        rij = self.con.execute(
            """SELECT AVG(s) g FROM (SELECT SUM(kosten_usd) s FROM ai_besluiten
                                      WHERE portefeuille = ? GROUP BY datum HAVING s > 0)""",
            (hoofd,),
        ).fetchone()
        per_nacht = float(rij["g"] or 0.0)
        # Reken met het hoogste van het gemiddelde en de laatste vijf nachten, plus
        # een marge: lopen de kosten op, dan komt de hoofdvraag anders toch tekort.
        recent = self.con.execute(
            """SELECT AVG(s) g FROM (SELECT SUM(kosten_usd) s FROM ai_besluiten WHERE portefeuille = ?
                                      GROUP BY datum HAVING s > 0 ORDER BY datum DESC LIMIT 5)""",
            (hoofd,),
        ).fetchone()
        per_nacht = max(per_nacht, float(recent["g"] or 0.0))
        marge = 1.0 + float(cfg["ai"]["budget"].get("reserve_marge_pct", 15)) / 100.0
        nachten = 0
        dag = date.fromisoformat(datum) + timedelta(days=1)
        # Een beslisdag telt voor de maand waarin de ronde draait: de dag erna.
        while (dag + timedelta(days=1)).strftime("%Y-%m") <= self.maand:
            if (dag + timedelta(days=1)).strftime("%Y-%m") == self.maand and beurs_open_volgens_kalender(cfg, dag.isoformat()):
                nachten += 1
            dag += timedelta(days=1)
        return per_nacht * nachten * marge

    def nachtkosten(self, portefeuille: str) -> float:
        rij = self.con.execute(
            """SELECT AVG(s) g FROM (SELECT SUM(kosten_usd) s FROM ai_besluiten
                                      WHERE portefeuille = ? GROUP BY datum HAVING s > 0)""",
            (portefeuille,),
        ).fetchone()
        return float(rij["g"] or 0.0)

    # ---------- overzicht ----------

    def samenvatting(self) -> str:
        uit = self.uitgegeven()
        regels = [
            f"AI-kosten {self.maand}: ${uit:.4f} van ${self.max_usd:.2f} "
            f"(nog ${self.resterend():.4f} over)"
        ]
        per_rol = self.con.execute(
            """SELECT rol, model, COUNT(*) n, SUM(tokens_in) ti, SUM(tokens_uit) tu,
                      SUM(kosten_usd) usd
               FROM ai_kosten WHERE maand = ?
               GROUP BY rol, model ORDER BY usd DESC""",
            (self.maand,),
        ).fetchall()
        for r in per_rol:
            regels.append(
                f"  {r['rol']:10s} {r['model']:18s} {r['n']:5d} aanroepen  "
                f"{r['ti']:>8d} in  {r['tu']:>7d} uit  ${r['usd']:.4f}"
            )
        if self.waarschuwt():
            regels.append(
                f"  LET OP: nog ${self.resterend():.2f} over deze maand."
            )
        return "\n".join(regels)
