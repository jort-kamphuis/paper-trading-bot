"""Instellingen inlezen uit config.yaml (en sleutels uit .env)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT = Path(__file__).resolve().parent.parent
CONFIG_PAD = PROJECT / "config.yaml"
DB_PAD = PROJECT / "data" / "trading.db"


def laad(pad: Path | None = None) -> dict[str, Any]:
    """Lees config.yaml in als een gewone dictionary.

    'ai_watchlist: alles' wordt hier uitgeschreven tot alle symbolen.
    """
    pad = pad or CONFIG_PAD
    with open(pad, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg.get("ai_watchlist") == "alles":
        cfg["ai_watchlist"] = alle_symbolen(cfg)
    return cfg


def laad_env() -> None:
    """Lees .env in, als dat bestand bestaat. Zet niets over wat al in de omgeving staat."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PROJECT / ".env", override=False)


def sleutel(naam: str) -> str | None:
    """Haal een sleutel uit de omgeving. Geeft None als hij er niet is."""
    laad_env()
    waarde = os.environ.get(naam, "").strip()
    return waarde or None


def alle_symbolen(cfg: dict[str, Any]) -> list[str]:
    """Alle symbolen van de watchlist, in een vaste volgorde."""
    w = cfg["watchlist"]
    return list(w["aandelen"]) + list(w["etfs"]) + list(w["crypto"])


def aandelen_symbolen(cfg: dict[str, Any]) -> list[str]:
    w = cfg["watchlist"]
    return list(w["aandelen"]) + list(w["etfs"])


def crypto_symbolen(cfg: dict[str, Any]) -> list[str]:
    return list(cfg["watchlist"]["crypto"])


def is_crypto(symbool: str) -> bool:
    """Crypto herken je aan de schuine streep, bijvoorbeeld BTC/USDT."""
    return "/" in symbool
