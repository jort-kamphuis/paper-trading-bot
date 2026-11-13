"""De challenger: een voorgestelde verbetering eerlijk testen op nep-geld.

Regel van de meting: een lopende portefeuille wordt nooit aangepast. Daarom:

1. Het weekrapport stelt hooguit een verbetering voor: een getal in de
   instellingen van een regelstrategie, binnen vaste grenzen (config.yaml,
   'challenger.toegestaan').
2. Eerst een backtest (bot/challenger_toets.py): kiezen op de eerdere jaren,
   controleren op het laatste jaar. Doet het voorstel het in een van beide niet
   beter, dan start het niet.
3. Dan een eerlijk A/B-paar: twee nieuwe portefeuilles op dezelfde dag met
   hetzelfde geld. A met de huidige instellingen, B met de voorgestelde.
4. Na 8 weken: alleen bij een betekenisvol verschil (B beter en p-waarde 0,05
   of lager, met een blok-bootstrap) volgt een vraag om akkoord. Anders stopt
   de proef.
5. Na /akkoord gaat B verder als NIEUWE, vaste portefeuille. Het origineel en
   config.yaml blijven ongewijzigd. A stopt (die was een kopie van het origineel).

Er loopt hooguit een challenger tegelijk.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from bot import statistiek
from bot.tekst import getal

# A en B draaien zolang de proef loopt of op akkoord wacht.
ACTIEF = ("loopt", "wacht_op_akkoord")


@dataclass
class Voorstel:
    strategie: str
    parameter: str
    nieuwe_waarde: float
    onderbouwing: str


def _mooi(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else f"{x:g}"


# ------------------------------------------------------------ voorstel toetsen


def toets_voorstel(cfg: dict, voorstel: Voorstel, loopt_al: bool) -> str | None:
    """Mag dit voorstel getest worden? Geeft de reden als het niet mag, anders None."""
    if loopt_al:
        return "er loopt al een challenger; er loopt er hooguit een tegelijk"
    toegestaan = cfg["challenger"]["toegestaan"]
    if voorstel.strategie not in toegestaan:
        return f"strategie '{voorstel.strategie}' mag niet worden aangepast"
    grenzen = toegestaan[voorstel.strategie].get(voorstel.parameter)
    if grenzen is None:
        return f"instelling '{voorstel.parameter}' van {voorstel.strategie} mag niet worden aangepast"
    waarde = voorstel.nieuwe_waarde
    if grenzen.get("geheel") and float(waarde) != int(waarde):
        return f"{voorstel.parameter} moet een heel getal zijn"
    if not (grenzen["min"] <= waarde <= grenzen["max"]):
        return f"{voorstel.parameter} moet tussen {grenzen['min']} en {grenzen['max']} liggen"
    huidig = cfg["strategieen"][voorstel.strategie][voorstel.parameter]
    if float(huidig) == float(waarde):
        return f"{voorstel.parameter} staat al op {huidig}"

    nieuw = dict(cfg["strategieen"][voorstel.strategie])
    nieuw[voorstel.parameter] = waarde
    if voorstel.strategie == "trend" and not nieuw["snelle_ma"] < nieuw["langzame_ma"]:
        return "het snelle gemiddelde moet korter zijn dan het langzame"
    if voorstel.strategie == "mean_reversion" and not nieuw["verkoop_boven_ma"] < nieuw["trendfilter_ma"]:
        return "het verkoopgemiddelde moet korter zijn dan het trendfilter"
    if voorstel.strategie == "momentum" and not nieuw["aantal_posities"] <= nieuw["verkoop_buiten_top"]:
        return "het aantal posities mag niet groter zijn dan de verkoopgrens"
    if not voorstel.onderbouwing.strip():
        return "een voorstel moet een onderbouwing hebben"
    return None


def instellingen_paar(cfg: dict, voorstel: Voorstel) -> tuple[dict, dict]:
    """De instellingen van A (zoals nu) en B (met het voorstel)."""
    huidig = dict(cfg["strategieen"][voorstel.strategie])
    huidig.pop("actief", None)
    nieuw = dict(huidig)
    grenzen = cfg["challenger"]["toegestaan"][voorstel.strategie][voorstel.parameter]
    w = voorstel.nieuwe_waarde
    nieuw[voorstel.parameter] = int(w) if grenzen.get("geheel") else float(w)
    return huidig, nieuw


# ------------------------------------------------------------ starten en lopen


def actieve(con: sqlite3.Connection) -> list[sqlite3.Row]:
    plekken = ",".join("?" * len(ACTIEF))
    return con.execute(
        f"SELECT * FROM challengers WHERE status IN ({plekken}) ORDER BY id", ACTIEF
    ).fetchall()


def start(
    con: sqlite3.Connection, cfg: dict, voorstel: Voorstel, voorgesteld_op: str, gestart_op: str,
    toets: str | None = None,
) -> int:
    """Leg een nieuwe challenger vast. De portefeuilles maakt de motor bij de volgende ronde."""
    huidig, nieuw = instellingen_paar(cfg, voorstel)
    import uuid

    tijdelijk = uuid.uuid4().hex  # de echte namen hangen af van het nummer hieronder
    onderbouwing = voorstel.onderbouwing.strip()
    if toets:
        onderbouwing += f"\nBacktest: {toets}"
    cur = con.execute(
        """INSERT INTO challengers
           (strategie, parameter, oude_waarde, nieuwe_waarde, onderbouwing,
            portefeuille, portefeuille_a, instellingen_a, instellingen_b,
            voorgesteld_op, gestart_op, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?, 'loopt')""",
        (
            voorstel.strategie, voorstel.parameter,
            float(huidig[voorstel.parameter]), float(nieuw[voorstel.parameter]),
            onderbouwing, f"b-{tijdelijk}", f"a-{tijdelijk}",
            json.dumps(huidig), json.dumps(nieuw), voorgesteld_op, gestart_op,
        ),
    )
    nummer = int(cur.lastrowid)
    con.execute(
        "UPDATE challengers SET portefeuille = ?, portefeuille_a = ? WHERE id = ?",
        (f"{voorstel.strategie}_B{nummer}", f"{voorstel.strategie}_A{nummer}", nummer),
    )
    con.commit()
    return nummer


def strategieen_voor(con: sqlite3.Connection) -> list[tuple[str, str, dict, str]]:
    """Voor de motor: (strategie, portefeuillenaam, instellingen, gestart_op).

    Een lopende proef: A en B. Een aangenomen verbetering: alleen B, als vaste
    eigen portefeuille.
    """
    uit = []
    for c in actieve(con):
        uit.append((c["strategie"], c["portefeuille_a"], json.loads(c["instellingen_a"]), c["gestart_op"]))
        uit.append((c["strategie"], c["portefeuille"], json.loads(c["instellingen_b"]), c["gestart_op"]))
    for c in con.execute("SELECT * FROM challengers WHERE status = 'aangenomen' ORDER BY id"):
        uit.append((c["strategie"], c["portefeuille"], json.loads(c["instellingen_b"]), c["gestart_op"]))
    return uit


def _sluit(con: sqlite3.Connection, *namen: str) -> None:
    for naam in namen:
        con.execute("UPDATE portefeuilles SET actief = 0 WHERE naam = ?", (naam,))


# ------------------------------------------------------------------ beoordelen


@dataclass
class Uitslag:
    challenger_id: int
    beter: bool  # True: er volgt een vraag om akkoord
    tekst: str


def _waarden(con: sqlite3.Connection, pf: str, tot: str) -> dict[str, float]:
    return {
        r["datum"]: r["totaal_usd"]
        for r in con.execute(
            "SELECT datum, totaal_usd FROM waarde WHERE portefeuille = ? AND datum <= ?",
            (pf, tot),
        )
    }


def vergelijk_paar(
    con: sqlite3.Connection, c: sqlite3.Row, tot: str, kalender: list[str]
) -> tuple[float, float, float | None] | None:
    """Rendement van A en B over dezelfde dagen, plus de p-waarde dat B beter is."""
    a = _waarden(con, c["portefeuille_a"], tot)
    b = _waarden(con, c["portefeuille"], tot)
    gedeeld = [d for d in kalender if d in a and d in b] or sorted(set(a) & set(b))
    if len(gedeeld) < 2:
        return None
    wa = [a[d] for d in gedeeld]
    wb = [b[d] for d in gedeeld]
    ra = statistiek.rendement_pct(wa[0], wa[-1])
    rb = statistiek.rendement_pct(wb[0], wb[-1])
    p = statistiek.vergelijk_reeksen(
        list(statistiek.dagrendementen(wb)), list(statistiek.dagrendementen(wa))
    )
    return ra, rb, p


def beoordeel(con: sqlite3.Connection, cfg: dict, tot: str, kalender: list[str]) -> list[Uitslag]:
    """Beoordeel elke challenger die lang genoeg heeft gelopen."""
    weken = int(cfg["challenger"]["weken"])
    grens = float(cfg["challenger"].get("betekenisvol_p", 0.05))
    # Alleen in de proefweek: ook zonder betekenisvol verschil om akkoord
    # vragen, zodat de akkoordroute getest kan worden.
    altijd_vragen = bool(cfg["challenger"].get("altijd_vragen"))
    uitslagen: list[Uitslag] = []
    for c in actieve(con):
        if c["status"] != "loopt":
            continue
        klaar_op = date.fromisoformat(c["gestart_op"]) + timedelta(weeks=weken)
        if date.fromisoformat(tot) < klaar_op:
            continue
        cijfers = vergelijk_paar(con, c, tot, kalender)
        if cijfers is None:
            continue
        ra, rb, p = cijfers
        if p is None and not altijd_vragen:
            # Nog te weinig beursdagen voor een eerlijke toets (zie
            # statistiek.blokgemiddelden_p_waarde): de proef loopt door.
            continue
        betekenisvol = rb > ra and p is not None and p <= grens
        p_tekst = "te weinig dagen om te toetsen" if p is None else f"p-waarde {getal(p, 2)}"
        tekst = (
            f"Challenger {c['id']} ({c['strategie']}: {c['parameter']} "
            f"{_mooi(c['oude_waarde'])} naar {_mooi(c['nieuwe_waarde'])}), {weken} weken: "
            f"huidig {getal(ra, 1, teken=True)}%, voorstel {getal(rb, 1, teken=True)}% ({p_tekst})."
        )
        if betekenisvol:
            tekst += " Het voorstel deed het betekenisvol beter."
        elif altijd_vragen:
            tekst += (
                " Geen betekenisvol verschil. Normaal stopt de proef dan; in de proefweek "
                "wordt toch om akkoord gevraagd, om de akkoordroute te testen."
            )
        if betekenisvol or altijd_vragen:
            con.execute(
                "UPDATE challengers SET status='wacht_op_akkoord', beoordeeld_op=?, uitslag=? WHERE id=?",
                (tot, tekst, c["id"]),
            )
        else:
            con.execute(
                "UPDATE challengers SET status='afgewezen', beoordeeld_op=?, uitslag=? WHERE id=?",
                (tot, tekst + " Geen betekenisvol verschil, dus de proef stopt.", c["id"]),
            )
            _sluit(con, c["portefeuille"], c["portefeuille_a"])
        uitslagen.append(Uitslag(challenger_id=c["id"], beter=betekenisvol or altijd_vragen, tekst=tekst))
    con.commit()
    return uitslagen


def vraag_om_akkoord(c: sqlite3.Row) -> str:
    return (
        f"{c['uitslag']}\n\n"
        f"Wil je dat de voorgestelde versie ({c['portefeuille']}) als eigen portefeuille "
        f"verder gaat? Het origineel ({c['strategie']}) en config.yaml blijven ongewijzigd.\n"
        f"Antwoord met:\n/akkoord {c['id']}\nof\n/afwijzen {c['id']}\n\n"
        f"Zolang je niet antwoordt, loopt de proef door."
    )


# ------------------------------------------------------ je antwoord verwerken


OPDRACHT = re.compile(r"^/(akkoord|afwijzen)\s+(\d+)\s*$")


def lees_opdracht(tekst: str) -> tuple[str, int] | None:
    """Herken /akkoord 3 of /afwijzen 3. Al het andere wordt genegeerd."""
    gevonden = OPDRACHT.match((tekst or "").strip().lower())
    if not gevonden:
        return None
    return gevonden.group(1), int(gevonden.group(2))


def verwerk(
    con: sqlite3.Connection,
    config_pad=None,  # niet meer gebruikt: config.yaml wordt nooit aangepast
    actie: str = "",
    challenger_id: int = 0,
    wanneer: str = "",
    datum: str = "",
    droog: bool = False,
) -> str:
    """Voer je /akkoord of /afwijzen uit. Geeft een antwoord in gewone taal terug.

    Een akkoord verandert nooit een lopende portefeuille en nooit config.yaml:
    de voorgestelde versie gaat verder als nieuwe portefeuille.
    'droog' (de proefweek) zet er alleen bij dat het een test was.
    """
    c = con.execute("SELECT * FROM challengers WHERE id = ?", (challenger_id,)).fetchone()
    if c is None:
        return f"Challenger {challenger_id} bestaat niet. Er is niets veranderd."
    if c["status"] != "wacht_op_akkoord":
        return (
            f"Challenger {challenger_id} wacht niet op een akkoord (status: {c['status']}). "
            f"Er is niets veranderd."
        )
    label = " (proefweek)" if droog else ""

    if actie == "afwijzen":
        con.execute(
            "UPDATE challengers SET status='afgewezen', uitslag=uitslag || ? WHERE id=?",
            (f" Afgewezen via Telegram ({wanneer}){label}.", challenger_id),
        )
        _sluit(con, c["portefeuille"], c["portefeuille_a"])
        con.commit()
        return f"Challenger {challenger_id} afgewezen{label}. Er is niets veranderd."

    con.execute(
        "UPDATE challengers SET status='aangenomen', uitslag=uitslag || ? WHERE id=?",
        (f" Aangenomen via Telegram ({wanneer}){label}.", challenger_id),
    )
    con.execute(
        """INSERT INTO wijzigingen (datum, strategie, parameter, oude_waarde, nieuwe_waarde,
                                    challenger_id, akkoord)
           VALUES (?,?,?,?,?,?,?)""",
        (datum, c["strategie"], c["parameter"], c["oude_waarde"], c["nieuwe_waarde"],
         challenger_id, f"Telegram, {wanneer}{label}: nieuwe portefeuille {c['portefeuille']}"),
    )
    _sluit(con, c["portefeuille_a"])  # A was een kopie van het origineel
    con.commit()
    return (
        f"Akkoord verwerkt{label}. {c['portefeuille']} ({c['strategie']} met "
        f"{c['parameter']} = {_mooi(c['nieuwe_waarde'])}) gaat verder als eigen portefeuille. "
        f"Het origineel {c['strategie']} en config.yaml blijven ongewijzigd."
    )


def nu_tekst() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
