"""Zet de motor op met alle strategieën en hun portefeuilles.

Dit staat apart zodat de backtest, de dagelijkse ronde en het overzicht
gegarandeerd met dezelfde opstelling werken.
"""

from __future__ import annotations

import sqlite3

from bot import config as conf
from bot.markt import Markt
from bot.motor import Motor
from bot.strategieen import maak_strategieen
from bot.strategieen.benchmarks import KoopEnHoud

# Onder deze sleutel onthoudt de bot wanneer de echte meting begon.
SLEUTEL_START = "start_datum"


def bouw_motor(
    con: sqlite3.Connection,
    cfg: dict,
    markt: Markt,
    wisselkoers,
    begin: str,
    stil: bool = True,
    met_ai: bool = False,
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

    for strategie in maak_strategieen(cfg):
        motor.voeg_toe(strategie, aanwezig, gestart_op=begin)

    if cfg["strategieen"]["benchmarks"]["actief"]:
        for symbool in cfg["strategieen"]["benchmarks"]["posities"]:
            if markt.heeft(symbool):
                motor.voeg_toe(KoopEnHoud(symbool), [symbool], gestart_op=begin)
            elif not stil:
                print(f"Meetlat {symbool} overgeslagen: geen koersen")

    if met_ai:
        from bot.ai.ronde import ai_portefeuilles
        from bot.strategieen.ai_analist import AIStrategie

        ai_symbolen = [s for s in cfg.get("ai_watchlist", []) if markt.heeft(s)]
        for naam, model in ai_portefeuilles(cfg):
            motor.voeg_toe(AIStrategie(naam, model_override=model), ai_symbolen, gestart_op=begin)

    return motor


def ai_symbolen(cfg: dict, markt: Markt) -> list[str]:
    return [s for s in cfg.get("ai_watchlist", []) if markt.heeft(s)]
