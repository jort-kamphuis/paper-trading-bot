"""De AI-analist: vijf rollen die samen tot een beslissing komen.

De volgorde is: technisch analist en nieuwsanalist verzamelen, bull en bear
debatteren, risk manager beslist. Elke stap wordt opgeslagen, zodat je
achteraf kunt lezen waarom er iets is gebeurd.

Drie dingen die de kosten laag houden zonder dat de kwaliteit eronder lijdt:
1. Zien beide analisten niets bijzonders en hebben we geen positie, dan slaan
   we het debat over. De uitkomst zou toch NIETS DOEN zijn geweest, en het
   debat draait op het duurste model.
2. Elke rol krijgt een maximum aan ruimte om te schrijven.
3. Denkwerk staat uit waar er niets te overwegen valt.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bot.ai import rollen
from bot.ai.client import Antwoord, ClaudeClient
from bot.data import nieuws as mod_nieuws
from bot.markt import Markt

# Onder deze zekerheid handelt de risk manager niet. Staat hier en niet in
# config.yaml omdat het samen met de instructie van de risk manager hoort.
MIN_ZEKERHEID = 0.6


@dataclass
class Besluit:
    symbool: str
    datum: str
    actie: str  # KOOP, VERKOOP of NIETS
    onderbouwing: str
    zekerheid: float
    stappen: dict[str, dict] = field(default_factory=dict)
    koppen: list[dict] = field(default_factory=list)
    koppen_gezien: int = 0
    kosten_usd: float = 0.0
    overgeslagen: str | None = None


class AIAnalist:
    def __init__(
        self,
        con: sqlite3.Connection,
        cfg: dict,
        client: ClaudeClient,
        model_override: str | None = None,
    ):
        self.con = con
        self.cfg = cfg
        self.client = client
        # De controleportefeuille draait alles op een goedkoop model. Daarmee
        # meet je of het duurdere model zijn geld waard is.
        self.model_override = model_override
        self.ai = cfg["ai"]

    def _model(self, rol: str) -> str:
        return self.model_override or self.ai["rollen"][rol]

    def _denkwerk(self, rol: str) -> tuple[str, str]:
        d = self.ai["denkwerk"][rol]
        return d["denken"], d["inspanning"]

    def _vraag(self, rol: str, systeem: str, bericht: str, schema: dict) -> Antwoord:
        denken, inspanning = self._denkwerk(rol)
        return self.client.vraag(
            rol=rol,
            model=self._model(rol),
            systeem=systeem,
            bericht=bericht,
            max_tokens=int(self.ai["max_tokens"][rol]),
            schema=schema,
            denken=denken,
            inspanning=inspanning,
        )

    # ---------- de kengetallen die de technisch analist ziet ----------

    def _kengetallen(self, symbool: str, datum: str, markt: Markt) -> dict[str, Any] | None:
        bar = markt.bar(symbool, datum)
        if bar is None:
            return None
        getallen: dict[str, Any] = {"slotkoers": round(bar.slot, 4)}
        for naam, label in [
            ("rsi14", "RSI (14 dagen, 0-100)"),
            ("ma20", "gemiddelde over 20 dagen"),
            ("ma100", "gemiddelde over 100 dagen"),
            ("rend60", "rendement over 60 dagen in procent"),
        ]:
            waarde = markt.kengetal(symbool, datum, naam)
            getallen[label] = None if waarde is None else round(waarde, 2)

        ma20 = markt.kengetal(symbool, datum, "ma20")
        ma100 = markt.kengetal(symbool, datum, "ma100")
        if ma20 is not None and ma100 is not None:
            getallen["koers staat boven beide gemiddelden"] = (
                bar.slot > ma20 and bar.slot > ma100
            )
            getallen["korte trend boven lange trend"] = ma20 > ma100
        return getallen

    # ---------- het besluit ----------

    def beslis(
        self,
        symbool: str,
        datum: str,
        markt: Markt,
        heeft_positie: bool,
        kas_eur: float,
        max_per_trade_eur: float,
        nieuws_tot: str,
    ) -> Besluit:
        """Laat de vijf rollen hun werk doen en geef het besluit terug."""
        leeg = Besluit(symbool, datum, "NIETS", "", 0.0)

        kengetallen = self._kengetallen(symbool, datum, markt)
        if kengetallen is None:
            leeg.overgeslagen = "geen koers op deze dag"
            return leeg

        # ---- 1. technisch analist ----
        technisch = self._vraag(
            "technisch",
            rollen.SYSTEEM_TECHNISCH,
            rollen.bericht_technisch(symbool, datum, kengetallen),
            rollen.SCHEMA_ANALIST,
        )
        if technisch.probleem():
            leeg.overgeslagen = technisch.probleem()
            leeg.kosten_usd = technisch.usd
            return leeg

        # ---- 2. nieuwsanalist ----
        koppen = [
            dict(r)
            for r in mod_nieuws.koppen_voor(
                self.con,
                symbool,
                voor_moment=nieuws_tot,
                max_dagen_oud=int(self.ai["nieuws"]["max_dagen_oud"]),
                max_aantal=int(self.ai["nieuws"]["max_koppen"]),
            )
        ]
        nieuws_antwoord = self._vraag(
            "nieuws",
            rollen.SYSTEEM_NIEUWS,
            rollen.bericht_nieuws(symbool, datum, koppen),
            rollen.SCHEMA_ANALIST,
        )
        kosten = technisch.usd + nieuws_antwoord.usd
        nieuws_data = nieuws_antwoord.data or {
            "signaal": "neutraal",
            "samenvatting": "geen bruikbaar antwoord",
            "zekerheid": 0,
        }

        stappen = {"technisch": technisch.data, "nieuws": nieuws_data}

        # ---- de goedkope afslag ----
        beide_neutraal = (
            technisch.data.get("signaal") == "neutraal"
            and nieuws_data.get("signaal") == "neutraal"
        )
        if beide_neutraal and not heeft_positie:
            return Besluit(
                symbool=symbool,
                datum=datum,
                actie="NIETS",
                onderbouwing=(
                    "Beide analisten zien niets bijzonders en we hebben geen "
                    "positie, dus er valt niets te beslissen."
                ),
                zekerheid=0.0,
                stappen=stappen,
                koppen=koppen,
                koppen_gezien=len(koppen),
                kosten_usd=kosten,
                overgeslagen="debat overgeslagen: beide analisten neutraal",
            )

        # ---- 3. en 4. het debat ----
        debat_bericht = rollen.bericht_debat(
            symbool, datum, technisch.data, nieuws_data, heeft_positie
        )
        bull = self._vraag(
            "bull", rollen.SYSTEEM_BULL, debat_bericht, rollen.SCHEMA_DEBAT
        )
        bear = self._vraag(
            "bear", rollen.SYSTEEM_BEAR, debat_bericht, rollen.SCHEMA_DEBAT
        )
        kosten += bull.usd + bear.usd
        stappen["bull"] = bull.data
        stappen["bear"] = bear.data

        # ---- 5. de risk manager ----
        risk = self._vraag(
            "risk",
            rollen.SYSTEEM_RISK,
            rollen.bericht_risk(
                symbool, datum, technisch.data, nieuws_data,
                bull.data or {}, bear.data or {},
                heeft_positie, kas_eur, max_per_trade_eur,
            ),
            rollen.SCHEMA_RISK,
        )
        kosten += risk.usd
        stappen["risk"] = risk.data

        if risk.probleem():
            return Besluit(
                symbool=symbool,
                datum=datum,
                actie="NIETS",
                onderbouwing=f"Geen beslissing mogelijk ({risk.probleem()}), dus doen we niets.",
                zekerheid=0.0,
                stappen=stappen,
                koppen=koppen,
                koppen_gezien=len(koppen),
                kosten_usd=kosten,
                overgeslagen=risk.probleem(),
            )

        actie = str(risk.data.get("actie", "NIETS")).upper()
        zekerheid = float(risk.data.get("zekerheid", 0) or 0)
        onderbouwing = str(risk.data.get("onderbouwing", "")).strip()

        # ---- de harde controles van de bot zelf ----
        # Het model mag niet beslissen wat onmogelijk is. Deze controles
        # staan hier en niet alleen in de instructie, want een instructie
        # is een verzoek en dit is een regel.
        if actie not in ("KOOP", "VERKOOP", "NIETS"):
            actie = "NIETS"
        if actie == "KOOP" and heeft_positie:
            actie, onderbouwing = "NIETS", (
                "De risk manager wilde kopen, maar die positie hebben we al. "
                + onderbouwing
            )
        if actie == "VERKOOP" and not heeft_positie:
            actie, onderbouwing = "NIETS", (
                "De risk manager wilde verkopen, maar die positie hebben we niet. "
                + onderbouwing
            )
        if actie == "KOOP" and kas_eur < max_per_trade_eur:
            actie, onderbouwing = "NIETS", (
                "Te weinig kas om te kopen. " + onderbouwing
            )
        if actie != "NIETS" and zekerheid < MIN_ZEKERHEID:
            actie, onderbouwing = "NIETS", (
                f"Zekerheid {zekerheid:.2f} ligt onder de grens van "
                f"{MIN_ZEKERHEID}, dus doen we niets. " + onderbouwing
            )

        return Besluit(
            symbool=symbool,
            datum=datum,
            actie=actie,
            onderbouwing=onderbouwing,
            zekerheid=zekerheid,
            stappen=stappen,
            koppen=koppen,
            koppen_gezien=len(koppen),
            kosten_usd=kosten,
        )
