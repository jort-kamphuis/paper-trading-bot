"""Proefweek of echte meting?

Tot en met de datum in config.proefweek.yaml draait de bot als proefweek: met
de instellingen uit dat bestand bovenop config.yaml, en met een eigen database.
Daarna geldt vanzelf weer alleen config.yaml en de echte database.

config.yaml verandert hierdoor nooit. Er hoeft na de proefweek dus ook niets
"teruggezet" te worden.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from bot import config as conf

PROEFWEEK_PAD = conf.PROJECT / "config.proefweek.yaml"


def _vandaag(nu: datetime | None = None) -> date:
    return (nu or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


def lees_proefweek(pad: Path | None = None) -> dict | None:
    pad = pad or PROEFWEEK_PAD
    if not pad.exists():
        return None
    with open(pad, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or None


def is_proefweek(nu: datetime | None = None, overlay: dict | None = None) -> bool:
    overlay = overlay if overlay is not None else lees_proefweek()
    if not overlay or "proefweek" not in overlay:
        return False
    tot = date.fromisoformat(str(overlay["proefweek"]["tot_en_met"]))
    return _vandaag(nu) <= tot


def _voeg_samen(basis: dict, extra: dict) -> dict:
    """Leg 'extra' over 'basis': per sleutel, en dieper in de onderdelen."""
    uit = copy.deepcopy(basis)
    for sleutel, waarde in extra.items():
        if isinstance(waarde, dict) and isinstance(uit.get(sleutel), dict):
            uit[sleutel] = _voeg_samen(uit[sleutel], waarde)
        else:
            uit[sleutel] = copy.deepcopy(waarde)
    return uit


def is_omgeschakeld() -> bool:
    """Is de echte meting al gestart (eventueel eerder dan gepland)?"""
    if not conf.DB_PAD.exists():
        return False
    import sqlite3

    con = sqlite3.connect(str(conf.DB_PAD))
    try:
        rij = con.execute(
            "SELECT 1 FROM instellingen WHERE sleutel = 'omgeschakeld_op'"
        ).fetchone()
    except sqlite3.Error:
        rij = None
    finally:
        con.close()
    return rij is not None


def laad(
    nu: datetime | None = None, overlay: dict | None = None, omgeschakeld: bool | None = None
) -> dict:
    """De instellingen die nu gelden: met of zonder proefweek.

    Het resultaat heeft altijd 'modus' ('proefweek' of 'echt') en 'database'.
    Is de echte meting al gestart (ook als dat eerder was dan gepland), dan
    geldt de proefweek niet meer.
    """
    basis = conf.laad()
    overlay = overlay if overlay is not None else lees_proefweek()
    if omgeschakeld is None:
        omgeschakeld = is_omgeschakeld()
    if omgeschakeld or not is_proefweek(nu, overlay):
        basis["modus"] = "echt"
        basis["database"] = str(conf.DB_PAD)
        basis["label"] = ""
        return basis

    cfg = _voeg_samen(basis, overlay)
    if cfg.get("ai_watchlist") == "alles":
        cfg["ai_watchlist"] = conf.alle_symbolen(cfg)
    cfg["modus"] = "proefweek"
    cfg["database"] = str(conf.PROJECT / overlay["proefweek"]["database"])
    cfg["label"] = overlay["proefweek"].get("label", "[PROEFWEEK]")
    return cfg


def database_pad(cfg: dict) -> Path:
    return Path(cfg.get("database") or conf.DB_PAD)


def met_label(cfg: dict, tekst: str) -> str:
    label = cfg.get("label") or ""
    return f"{label}\n{tekst}" if label else tekst
