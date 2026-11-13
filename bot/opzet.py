"""Zet de motor op met alle strategieën en hun portefeuilles.

Dit staat apart zodat de backtest, de dagelijkse ronde en het overzicht
gegarandeerd met dezelfde opstelling werken.

De portefeuilles van de echte meting:
* momentum, mean_reversion, trend: de regels op alle symbolen;
* momentum_ai, mean_reversion_ai, trend_ai: dezelfde regels op de AI-lijst
  (alleen als de AI niet alle symbolen volgt);
* benchmark_spy, benchmark_btc: alles in een keer in SPY of BTC;
* gelijk_alle (en gelijk_ai): gelijk verdeeld kopen en vasthouden;
* ai_analist, ai_controle_haiku, ai_enkel: de AI;
* challengers: lopende proeven en aangenomen verbeteringen.
"""

from __future__ import annotations

import copy
import sqlite3

from bot import config as conf
from bot.markt import Markt
from bot.motor import Motor
from bot.strategieen import MeanReversion, Momentum, TrendVolgend, maak_strategieen
from bot.strategieen.benchmarks import GelijkVerdeeld, KoopEnHoud

# Onder deze sleutel onthoudt de bot wanneer de echte meting begon.
SLEUTEL_START = "start_datum"

SOORTEN = {"momentum": Momentum, "mean_reversion": MeanReversion, "trend": TrendVolgend}


def ai_symbolen(cfg: dict, markt: Markt) -> list[str]:
    return [s for s in cfg.get("ai_watchlist", []) if markt.heeft(s)]


def ai_volgt_alles(cfg: dict) -> bool:
    return set(cfg.get("ai_watchlist", [])) >= set(conf.alle_symbolen(cfg))


def bouw_motor(
    con: sqlite3.Connection,
    cfg: dict,
    markt: Markt,
    wisselkoers,
    begin: str,
    stil: bool = True,
    met_ai: bool = False,
    met_challengers: bool = False,
) -> Motor:
    """Maak de motor met alle regelstrategieën en de meetlatten.

    met_ai staat standaard uit. De backtest mag de AI-analist nooit meenemen:
    het nieuws van toen is niet meer op te halen, dus elke AI-beslissing over
    het verleden zou gebaseerd zijn op nieuws van later.
    """
    motor = Motor(con, cfg, markt, wisselkoers)

    symbolen = conf.alle_symbolen(cfg)
    aanwezig = [s for s in symbolen if markt.heeft(s)]
    ontbreekt = [s for s in symbolen if not markt.heeft(s)]
    if ontbreekt and not stil:
        print(f"Geen koersen voor: {ontbreekt}")
    ai_lijst = ai_symbolen(cfg, markt)
    deellijst = bool(ai_lijst) and not ai_volgt_alles(cfg)

    # ---- de regels ----
    for strategie in maak_strategieen(cfg):
        motor.voeg_toe(strategie, aanwezig, gestart_op=begin)
    if deellijst and cfg["strategieen"].get("ook_op_ai_lijst"):
        for strategie in maak_strategieen(cfg):
            strategie.soort = strategie.naam
            strategie.naam = f"{strategie.naam}_ai"
            motor.voeg_toe(strategie, ai_lijst, gestart_op=begin)

    # ---- de meetlatten ----
    meetlat = cfg["strategieen"]["benchmarks"]
    if meetlat["actief"]:
        for symbool in meetlat["posities"]:
            if markt.heeft(symbool):
                motor.voeg_toe(KoopEnHoud(symbool), [symbool], gestart_op=begin)
            elif not stil:
                print(f"Meetlat {symbool} overgeslagen: geen koersen")
        if meetlat.get("gelijk_verdeeld"):
            motor.voeg_toe(GelijkVerdeeld("gelijk_alle", aanwezig), aanwezig, gestart_op=begin)
            if deellijst:
                motor.voeg_toe(GelijkVerdeeld("gelijk_ai", ai_lijst), ai_lijst, gestart_op=begin)

    # ---- de AI ----
    if met_ai:
        from bot.ai.ronde import ai_portefeuilles
        from bot.strategieen.ai_analist import AIStrategie

        for p in ai_portefeuilles(cfg):
            motor.voeg_toe(
                AIStrategie(p["naam"], model_override=p.get("model"), soort=p.get("soort", "debat")),
                ai_lijst,
                gestart_op=begin,
            )

    # ---- challengers ----
    if met_challengers:
        # Een lopende proef draait als paar: A (huidig) en B (voorstel), met de
        # instellingen zoals die bij de start zijn vastgelegd. Een aangenomen
        # verbetering draait door als eigen portefeuille.
        from bot.challenger import strategieen_voor

        for strategie, portefeuille, instellingen, gestart_op in strategieen_voor(con):
            soort = SOORTEN.get(strategie)
            if soort is None:
                continue
            exemplaar = soort(copy.deepcopy(instellingen))
            exemplaar.naam = portefeuille
            exemplaar.soort = strategie
            exemplaar.variant = "challenger"
            motor.voeg_toe(exemplaar, aanwezig, gestart_op=gestart_op)

    return motor
