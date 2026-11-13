"""De AI-analist: geeft per symbool een kans en een verwacht extra rendement.

Twee soorten:
* debat: nieuwsanalist, dan bull en bear, dan de risk manager die de kans geeft;
* enkel: een aanroep die alles zelf afweegt (test of het debat iets toevoegt).

De analist beslist niets. Wat er gekocht of verkocht wordt, beslist de code
(bot/ai/beslisregel.py) met vaste drempels. Zo meet je de kansen zelf, en niet
een model dat bang is om iets te doen (in de eerste proefronde koos de oude
risk manager 110 van de 110 keer "niets", terwijl de drempel van 0,6 niets
tegenhield).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bot.ai import rollen
from bot.ai.client import Antwoord, ClaudeClient
from bot.ai.kenmerken import referentie as referentie_van
from bot.ai.kenmerken import tabel, vraagsoort
from bot.data import nieuws as mod_nieuws
from bot.markt import Markt


@dataclass
class Beoordeling:
    symbool: str
    datum: str
    referentie: str
    kans: float | None = None
    verwacht_pct: float | None = None
    onderbouwing: str = ""
    belangrijkste_risico: str = ""
    stappen: dict[str, Any] = field(default_factory=dict)
    koppen: list[dict] = field(default_factory=list)
    kosten_usd: float = 0.0
    probleem: str | None = None  # waarom er geen bruikbare kans is
    vraag: str = ""  # relatief of absoluut (zie bot/ai/kenmerken.py)

    def __post_init__(self) -> None:
        if not self.referentie:
            self.referentie = referentie_van(self.symbool)
        if not self.vraag:
            self.vraag = vraagsoort(self.referentie)

    @property
    def bruikbaar(self) -> bool:
        return self.probleem is None and self.kans is not None and self.verwacht_pct is not None


class AIAnalist:
    def __init__(
        self,
        con: sqlite3.Connection,
        cfg: dict,
        client: ClaudeClient,
        model_override: str | None = None,
        soort: str = "debat",
    ):
        self.con = con
        self.cfg = cfg
        self.client = client
        self.model_override = model_override
        self.soort = soort
        self.ai = cfg["ai"]

    def _model(self, rol: str) -> str:
        return self.model_override or self.ai["rollen"][rol]

    def _vraag(self, rol: str, systeem: str, bericht: str, schema: dict) -> Antwoord:
        d = self.ai["denkwerk"][rol]
        return self.client.vraag(
            rol=rol,
            model=self._model(rol),
            systeem=systeem,
            bericht=bericht,
            max_tokens=int(self.ai["max_tokens"][rol]),
            schema=schema,
            denken=d["denken"],
            inspanning=d["inspanning"],
        )

    def beoordeel(
        self,
        symbool: str,
        datum: str,
        markt: Markt,
        positie: dict | None,
        portefeuille: dict,
        nieuws_tot: str,
    ) -> Beoordeling:
        kenmerken = tabel(markt, symbool, datum, self.con)
        b = Beoordeling(symbool=symbool, datum=datum, referentie=(kenmerken or {}).get("referentie", ""))
        if kenmerken is None:
            b.probleem = "geen koers op deze dag"
            return b
        b.stappen["kenmerken"] = kenmerken

        b.koppen = [
            dict(r)
            for r in mod_nieuws.koppen_voor(
                self.con, symbool, voor_moment=nieuws_tot,
                max_dagen_oud=int(self.ai["nieuws"]["max_dagen_oud"]),
                max_aantal=int(self.ai["nieuws"]["max_koppen"]),
            )
        ]

        if self.soort == "enkel":
            antwoord = self._vraag(
                "enkel", rollen.SYSTEEM_ENKEL,
                rollen.bericht_enkel(symbool, datum, kenmerken, b.koppen, positie, portefeuille),
                rollen.SCHEMA_KANS,
            )
            b.kosten_usd += antwoord.usd
            return self._vul_kans(b, antwoord, "enkel")

        nieuws = self._vraag(
            "nieuws", rollen.SYSTEEM_NIEUWS,
            rollen.bericht_nieuws(symbool, datum, b.koppen), rollen.SCHEMA_NIEUWS,
        )
        b.kosten_usd += nieuws.usd
        nieuws_data = nieuws.data or {
            "samenvatting": "geen bruikbaar antwoord van de nieuwsanalist",
            "belangrijkste_risico": "onbekend", "gebeurtenis_binnenkort": False,
        }
        b.stappen["nieuws"] = nieuws_data

        debat = rollen.bericht_debat(symbool, datum, kenmerken, b.koppen, nieuws_data, positie)
        bull = self._vraag("bull", rollen.SYSTEEM_BULL, debat, rollen.SCHEMA_DEBAT)
        bear = self._vraag("bear", rollen.SYSTEEM_BEAR, debat, rollen.SCHEMA_DEBAT)
        b.kosten_usd += bull.usd + bear.usd
        b.stappen["bull"] = bull.data
        b.stappen["bear"] = bear.data
        # Mislukt een van beide, dan zou de risk manager maar een kant horen. Dan
        # liever geen kans (en geen aankopen die nacht) dan een scheve.
        for antwoord in (bull, bear):
            if antwoord.probleem():
                b.probleem = antwoord.probleem()
                return b

        risk = self._vraag(
            "risk", rollen.SYSTEEM_RISK,
            rollen.bericht_risk(
                symbool, datum, kenmerken, b.koppen, nieuws_data,
                bull.data or {}, bear.data or {}, positie, portefeuille,
            ),
            rollen.SCHEMA_KANS,
        )
        b.kosten_usd += risk.usd
        return self._vul_kans(b, risk, "risk")

    @staticmethod
    def _vul_kans(b: Beoordeling, antwoord: Antwoord, rol: str) -> Beoordeling:
        if antwoord.probleem():
            b.probleem = antwoord.probleem()
            return b
        d = antwoord.data
        b.stappen[rol] = d
        try:
            kans = float(d.get("kans"))
            verwacht = float(d.get("verwacht_extra_rendement_pct"))
        except (TypeError, ValueError):
            b.probleem = f"{rol} gaf geen getal voor de kans of de verwachting"
            return b
        if not 0.0 <= kans <= 1.0:
            b.probleem = f"{rol} gaf een kans buiten 0 tot 1 ({kans})"
            return b
        b.kans = kans
        b.verwacht_pct = verwacht
        b.onderbouwing = str(d.get("onderbouwing", "")).strip()
        b.belangrijkste_risico = str(d.get("belangrijkste_risico", "")).strip()
        return b
