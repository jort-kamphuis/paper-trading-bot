"""De dagelijkse denkronde van de AI-analist.

Dit draait na de gewone dagronde, alleen voor de nieuwste dag, en alleen als
dat een beursdag was. Volgorde:

1. Elk symbool beoordelen: een kans en een verwacht extra rendement. Elke
   beoordeling wordt meteen opgeslagen, ook als er daarna niets gebeurt. Dat
   is de belangrijkste meting: achteraf wordt gescoord of de kansen klopten.
2. Pas als alles beoordeeld is, kiest de code (bot/ai/beslisregel.py) wat er
   gekocht en verkocht wordt.

Waarom alleen de nieuwste dag? Haalt de bot een paar gemiste dagen in, dan is
het nieuws van toen niet meer op te halen. Een beoordeling van drie dagen
geleden met het nieuws van vandaag zou vooruitkijken zijn.

Een beoordeling die al bestaat, wordt niet opnieuw gevraagd. Twee keer draaien
kost dus geen twee keer geld, en geeft dezelfde keuze.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from bot.ai import beslisregel
from bot.ai.analist import AIAnalist, Beoordeling
from bot.ai.budget import Budget, BudgetOp
from bot.ai.client import ClaudeClient
from bot.config import is_crypto
from bot.markt import Markt
from bot.portefeuille import Portefeuille

# Na zoveel fouten achter elkaar stoppen we. Meestal is er dan iets structureel
# mis, zoals een verkeerde sleutel, en heeft doorgaan geen zin.
MAX_FOUTEN_ACHTER_ELKAAR = 3


@dataclass
class Uitkomst:
    portefeuille: str
    datum: str
    beoordelingen: dict[str, Beoordeling] = field(default_factory=dict)
    orders: list = field(default_factory=list)
    herbevestigd: list[str] = field(default_factory=list)
    al_gedaan: int = 0
    fouten: list[str] = field(default_factory=list)
    gestopt: str | None = None
    kosten_usd: float = 0.0
    overgeslagen: str | None = None

    @property
    def compleet(self) -> bool:
        return self.gestopt is None and not self.fouten


def nieuws_grens(symbool: str, datum: str, nu: datetime | None = None) -> str:
    """Tot welk moment de AI nieuws mag zien voor een besluit over 'datum'.

    Een besluit van dag D wordt uitgevoerd op de openingskoers van de volgende
    handelsdag. Nieuws van na die opening kon de koers toen nog niet raken.
    De grens is dus het vroegste van: nu, of de opening waarop het besluit
    wordt uitgevoerd (crypto: 00:00 UTC de dag erna; aandelen: op zijn vroegst
    13:30 UTC de dag erna).
    """
    nu = nu or datetime.now(timezone.utc)
    volgende = datetime.fromisoformat(datum).replace(tzinfo=timezone.utc) + timedelta(days=1)
    opening = volgende if is_crypto(symbool) else volgende.replace(hour=13, minute=30)
    return min(nu, opening).isoformat()


# ---------------------------------------------------------- opslaan en inlezen


def _lees_beoordeling(con: sqlite3.Connection, pf: str, symbool: str, datum: str) -> Beoordeling | None:
    rij = con.execute(
        "SELECT * FROM ai_besluiten WHERE portefeuille=? AND symbool=? AND datum=?",
        (pf, symbool, datum),
    ).fetchone()
    if rij is None:
        return None
    return Beoordeling(
        symbool=symbool, datum=datum, referentie=rij["referentie"] or "",
        kans=rij["kans"], verwacht_pct=rij["verwacht_pct"],
        onderbouwing=rij["onderbouwing"] or "",
        stappen=json.loads(rij["stappen"] or "{}"),
        koppen=json.loads(rij["koppen"] or "[]"),
        kosten_usd=rij["kosten_usd"] or 0.0,
        probleem=rij["overgeslagen"],
        vraag=rij["vraag"] or "",
    )


def _bewaar_beoordeling(
    con: sqlite3.Connection, pf: str, b: Beoordeling, nieuws_tot: str, horizon: int
) -> None:
    koppen = [
        {"gepubliceerd": k.get("gepubliceerd"), "titel": k.get("titel"),
         "bron": k.get("bron"), "url": k.get("url")}
        for k in b.koppen
    ]
    con.execute(
        """INSERT INTO ai_besluiten
           (portefeuille, symbool, datum, actie, onderbouwing, zekerheid, stappen, koppen,
            koppen_gezien, kosten_usd, overgeslagen, nieuws_tot, gemaakt_op,
            kans, verwacht_pct, referentie, horizon_dagen, vraag)
           VALUES (?,?,?,'NIETS',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(portefeuille, symbool, datum) DO NOTHING""",
        (
            pf, b.symbool, b.datum, b.onderbouwing, b.kans,
            json.dumps(b.stappen, ensure_ascii=False), json.dumps(koppen, ensure_ascii=False),
            len(b.koppen), b.kosten_usd, b.probleem, nieuws_tot,
            datetime.now(timezone.utc).isoformat(),
            b.kans, b.verwacht_pct, b.referentie, horizon, b.vraag,
        ),
    )


def _portefeuille_context(pf: Portefeuille, markt: Markt, datum: str) -> dict:
    koersen = markt.slotkoersen_usd(datum)
    waarde = pf.totale_waarde(koersen)
    crypto = sum(p.aantal * koersen.get(s, 0.0) for s, p in pf.posities.items() if is_crypto(s))
    return {
        "posities": len(pf.posities),
        "max_posities": pf.max_posities or 10,
        "belegd_pct": 100.0 * pf.posities_waarde(koersen) / waarde if waarde else 0.0,
        "crypto_pct": 100.0 * crypto / waarde if waarde else 0.0,
    }


def _positie_context(pf: Portefeuille, symbool: str, markt: Markt, datum: str) -> dict | None:
    p = pf.posities.get(symbool)
    if p is None:
        return None
    slot = markt.slot_op_of_voor(symbool, datum) or 0.0
    waarde = p.aantal * slot + p.dividend_usd
    return {
        "gekocht_op": p.gekocht_op,
        "koopprijs": p.koopprijs,
        "resultaat_pct": 100.0 * (waarde / p.kostprijs_usd - 1.0) if p.kostprijs_usd else 0.0,
        "dagen": (date.fromisoformat(datum) - date.fromisoformat(p.gekocht_op)).days,
        "koopreden": p.koopreden,
    }


# ------------------------------------------------------------------ de ronde


def denk_na(
    con: sqlite3.Connection,
    cfg: dict,
    client: ClaudeClient,
    markt: Markt,
    portefeuille: str,
    datum: str,
    symbolen: list[str],
    model_override: str | None = None,
    soort: str = "debat",
    nieuws_tot: str | None = None,
    stil: bool = False,
) -> Uitkomst:
    """Laat een AI-portefeuille alle symbolen beoordelen en dan kiezen."""
    uit = Uitkomst(portefeuille=portefeuille, datum=datum)
    kalender = (cfg.get("rapport") or {}).get("kalender_symbool", "SPY")
    if markt.heeft(kalender) and not markt.is_handelsdag(kalender, datum):
        uit.overgeslagen = f"{datum} was geen beursdag; de AI beslist alleen op beursdagen"
        return uit

    pf = Portefeuille.uit_database(con, portefeuille, cfg["algemeen"])
    if pf is None:
        uit.gestopt = f"portefeuille {portefeuille} bestaat nog niet"
        return uit

    regels = cfg["ai"]["beslisregel"]
    horizon = int(regels["horizon_kalenderdagen"])
    plafond = datetime.fromisoformat(nieuws_tot) if nieuws_tot else None
    analist = AIAnalist(con, cfg, client, model_override=model_override, soort=soort)
    context = _portefeuille_context(pf, markt, datum)
    fouten_achter_elkaar = 0

    # ---- 1. alles beoordelen ----
    for symbool in symbolen:
        bestaand = _lees_beoordeling(con, portefeuille, symbool, datum)
        if bestaand is not None:
            uit.al_gedaan += 1
            uit.beoordelingen[symbool] = bestaand
            continue

        grens = nieuws_grens(symbool, datum, nu=plafond)
        try:
            b = analist.beoordeel(
                symbool=symbool, datum=datum, markt=markt,
                positie=_positie_context(pf, symbool, markt, datum),
                portefeuille=context, nieuws_tot=grens,
            )
        except BudgetOp as fout:
            con.commit()  # wat al is uitgegeven, staat geboekt
            uit.gestopt = str(fout)
            break
        except Exception as fout:  # netwerk, API, onverwacht antwoord
            con.commit()
            melding = f"{symbool}: {type(fout).__name__}: {str(fout)[:200]}"
            uit.fouten.append(melding)
            if not stil:
                print(f"    fout bij {melding}")
            fouten_achter_elkaar += 1
            if fouten_achter_elkaar >= MAX_FOUTEN_ACHTER_ELKAAR:
                uit.gestopt = f"{MAX_FOUTEN_ACHTER_ELKAAR} fouten achter elkaar, gestopt. Laatste: {melding}"
                break
            continue

        fouten_achter_elkaar = 0
        _bewaar_beoordeling(con, portefeuille, b, grens, horizon)
        con.commit()  # na elk symbool: het geld is uitgegeven, dus de beoordeling blijft
        uit.beoordelingen[symbool] = b
        uit.kosten_usd += b.kosten_usd
        if not stil:
            if b.bruikbaar:
                print(f"    {symbool:10s} kans {b.kans:.2f}  verwacht {b.verwacht_pct:+.1f}%  ${b.kosten_usd:.4f}")
            else:
                print(f"    {symbool:10s} geen kans ({b.probleem})  ${b.kosten_usd:.4f}")

    # ---- 2. kiezen ----
    wachtend = con.execute(
        "SELECT symbool, actie FROM wachtrij WHERE portefeuille = ?", (portefeuille,)
    ).fetchall()
    keuze = beslisregel.kies(
        beoordelingen=uit.beoordelingen,
        posities=pf.posities,
        wachtende_kopen={r["symbool"] for r in wachtend if r["actie"] == "KOOP"},
        wachtende_verkopen={r["symbool"] for r in wachtend if r["actie"] == "VERKOOP"},
        datum=datum,
        regels=regels,
        max_posities=pf.max_posities or 10,
        kopen_toegestaan=uit.compleet,
    )
    for order in keuze.orders:
        con.execute(
            """INSERT INTO wachtrij (portefeuille, symbool, actie, reden, zekerheid, besluit_datum)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(portefeuille, symbool, actie, besluit_datum) DO NOTHING""",
            (portefeuille, order.symbool, order.actie, order.reden[:1000], order.zekerheid, datum),
        )
        con.execute(
            "UPDATE ai_besluiten SET actie=? WHERE portefeuille=? AND symbool=? AND datum=?",
            (order.actie, portefeuille, order.symbool, datum),
        )
    for symbool in keuze.herbevestigd:
        con.execute(
            "UPDATE posities SET bevestigd_op=? WHERE portefeuille=? AND symbool=?",
            (datum, portefeuille, symbool),
        )
    con.commit()
    uit.orders = keuze.orders
    uit.herbevestigd = keuze.herbevestigd
    return uit


def maak_client(con: sqlite3.Connection, cfg: dict, vandaag: str | None = None) -> ClaudeClient:
    return ClaudeClient(
        Budget(con, cfg, vandaag=vandaag),
        pauze_seconden=float(cfg.get("ai", {}).get("pauze_seconden", 0) or 0),
    )


def ai_portefeuilles(cfg: dict, vandaag: date | None = None) -> list[dict]:
    """De AI-portefeuilles: naam, soort (debat of enkel) en eventueel een model voor alle rollen.

    Een portefeuille met 'tot_en_met' (zoals de Opus-proef) draait tot die datum.
    """
    if not cfg.get("ai", {}).get("actief"):
        return []
    vandaag = vandaag or datetime.now(timezone.utc).date()
    uit = []
    for p in cfg["ai"].get("portefeuilles") or []:
        tot = p.get("tot_en_met")
        if tot and vandaag > date.fromisoformat(str(tot)):
            continue
        uit.append({"naam": str(p["naam"]), "soort": p.get("soort", "debat"), "model": p.get("model")})
    return uit
