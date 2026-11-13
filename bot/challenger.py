"""De challenger: een voorgestelde verbetering eerlijk testen op nep-geld.

Zo werkt het:
1. Het weekrapport stelt hooguit een verbetering voor: een getal in de
   instellingen van een regelstrategie, binnen vaste grenzen (config.yaml,
   onder 'challenger.toegestaan').
2. De bot start dan twee nieuwe portefeuilles op dezelfde dag, elk met 1.000
   euro: A met de huidige instellingen, B met de voorgestelde. Verder zijn ze
   precies gelijk. Zo vergelijk je een verschil in instelling en niets anders.
3. Na het aantal weken uit config.yaml (standaard 4) vergelijkt de bot A en B.
   Is B beter, dan vraagt hij je akkoord via Telegram. Is B niet beter, dan
   stopt de proef.
4. Alleen na jouw /akkoord wordt de instelling in config.yaml aangepast. Dat
   gebeurt in een eigen commit, staat in de tabel 'wijzigingen' en je krijgt
   een bevestiging. Zonder akkoord verandert er niets.

Er loopt hooguit een challenger tegelijk.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bot import statistiek

ACTIEF = ("loopt", "wacht_op_akkoord")


@dataclass
class Voorstel:
    strategie: str
    parameter: str
    nieuwe_waarde: float
    onderbouwing: str


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
    if voorstel.strategie == "mean_reversion" and not nieuw["koop_onder"] < nieuw["verkoop_boven"]:
        return "de koopgrens moet onder de verkoopgrens liggen"
    if voorstel.strategie == "momentum" and not nieuw["aantal_posities"] <= nieuw["verkoop_buiten_top"]:
        return "het aantal posities mag niet groter zijn dan de verkoopgrens"
    if not voorstel.onderbouwing.strip():
        return "een voorstel moet een onderbouwing hebben"
    return None


# ------------------------------------------------------------ starten en lopen


def actieve(con: sqlite3.Connection) -> list[sqlite3.Row]:
    plekken = ",".join("?" * len(ACTIEF))
    return con.execute(
        f"SELECT * FROM challengers WHERE status IN ({plekken}) ORDER BY id", ACTIEF
    ).fetchall()


def start(
    con: sqlite3.Connection, cfg: dict, voorstel: Voorstel, voorgesteld_op: str, gestart_op: str
) -> int:
    """Leg een nieuwe challenger vast. De portefeuilles maakt de motor bij de volgende ronde."""
    huidig = dict(cfg["strategieen"][voorstel.strategie])
    huidig.pop("actief", None)
    nieuw = dict(huidig)
    waarde = voorstel.nieuwe_waarde
    grenzen = cfg["challenger"]["toegestaan"][voorstel.strategie][voorstel.parameter]
    nieuw[voorstel.parameter] = int(waarde) if grenzen.get("geheel") else float(waarde)

    import uuid

    tijdelijk = uuid.uuid4().hex  # de echte namen hangen af van het nummer hieronder
    cur = con.execute(
        """INSERT INTO challengers
           (strategie, parameter, oude_waarde, nieuwe_waarde, onderbouwing,
            portefeuille, portefeuille_a, instellingen_a, instellingen_b,
            voorgesteld_op, gestart_op, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?, 'loopt')""",
        (
            voorstel.strategie, voorstel.parameter,
            float(huidig[voorstel.parameter]), float(nieuw[voorstel.parameter]),
            voorstel.onderbouwing.strip(),
            f"b-{tijdelijk}", f"a-{tijdelijk}",
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
    """Voor de motor: (strategie, portefeuillenaam, instellingen, gestart_op) per lopend paar."""
    uit = []
    for c in actieve(con):
        uit.append((c["strategie"], c["portefeuille_a"], json.loads(c["instellingen_a"]), c["gestart_op"]))
        uit.append((c["strategie"], c["portefeuille"], json.loads(c["instellingen_b"]), c["gestart_op"]))
    return uit


def _sluit_portefeuilles(con: sqlite3.Connection, c: sqlite3.Row) -> None:
    con.execute(
        "UPDATE portefeuilles SET actief = 0 WHERE naam IN (?, ?)",
        (c["portefeuille"], c["portefeuille_a"]),
    )


# ------------------------------------------------------------------ beoordelen


@dataclass
class Uitslag:
    challenger_id: int
    beter: bool
    tekst: str


def _waarden(con: sqlite3.Connection, pf: str, tot: str) -> dict[str, float]:
    return {
        r["datum"]: r["totaal_eur"]
        for r in con.execute(
            "SELECT datum, totaal_eur FROM waarde WHERE portefeuille = ? AND datum <= ?",
            (pf, tot),
        )
    }


def vergelijk_paar(
    con: sqlite3.Connection, c: sqlite3.Row, tot: str, kalender: list[str]
) -> tuple[float, float, float | None] | None:
    """Rendement van A en B over dezelfde dagen, plus de p-waarde van het verschil."""
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
    # Alleen in de proefweek: ook als B niet beter is om akkoord vragen, zodat
    # de akkoordroute getest kan worden. In de echte meting staat dit uit.
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
        beter = rb > ra
        p_tekst = (
            "te weinig dagen om te toetsen" if p is None
            else f"p-waarde {p:.2f}: " + ("dat is betekenisvol" if p <= 0.05 else "dat kan nog gewoon geluk zijn")
        )
        tekst = (
            f"Challenger {c['id']} ({c['strategie']}: {c['parameter']} "
            f"{_mooi(c['oude_waarde'])} naar {_mooi(c['nieuwe_waarde'])}), {weken} weken: "
            f"huidig {ra:+.1f}%, voorstel {rb:+.1f}% ({p_tekst})."
        )
        if not beter and altijd_vragen:
            tekst += (
                " B was niet beter. Normaal stopt de proef dan; in de proefweek wordt "
                "toch om akkoord gevraagd, om de akkoordroute te testen."
            )
        if beter or altijd_vragen:
            con.execute(
                "UPDATE challengers SET status='wacht_op_akkoord', beoordeeld_op=?, uitslag=? WHERE id=?",
                (tot, tekst, c["id"]),
            )
            beter = True  # er volgt een vraag om akkoord
        else:
            con.execute(
                "UPDATE challengers SET status='afgewezen', beoordeeld_op=?, uitslag=? WHERE id=?",
                (tot, tekst + " Niet beter, dus de proef stopt.", c["id"]),
            )
            _sluit_portefeuilles(con, c)
        uitslagen.append(Uitslag(challenger_id=c["id"], beter=beter, tekst=tekst))
    con.commit()
    return uitslagen


def _mooi(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else f"{x:g}"


def vraag_om_akkoord(c: sqlite3.Row) -> str:
    return (
        f"{c['uitslag']}\n\n"
        f"Wil je dat dit de standaard wordt? Dan wordt in config.yaml "
        f"{c['strategie']}.{c['parameter']} {_mooi(c['oude_waarde'])} -> {_mooi(c['nieuwe_waarde'])}.\n"
        f"Antwoord met:\n/akkoord {c['id']}\nof\n/afwijzen {c['id']}\n\n"
        f"Zolang je niet antwoordt, verandert er niets en loopt de proef door."
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
    config_pad: Path,
    actie: str,
    challenger_id: int,
    wanneer: str,
    datum: str,
    droog: bool = False,
) -> str:
    """Voer je /akkoord of /afwijzen uit. Geeft een antwoord in gewone taal terug.

    Met droog=True (de proefweek) wordt alles verwerkt en vastgelegd, behalve het
    aanpassen van config.yaml.
    """
    c = con.execute("SELECT * FROM challengers WHERE id = ?", (challenger_id,)).fetchone()
    if c is None:
        return f"Challenger {challenger_id} bestaat niet. Er is niets veranderd."
    if c["status"] != "wacht_op_akkoord":
        return (
            f"Challenger {challenger_id} wacht niet op een akkoord (status: {c['status']}). "
            f"Er is niets veranderd."
        )

    if actie == "afwijzen":
        con.execute(
            "UPDATE challengers SET status='afgewezen', uitslag=uitslag || ? WHERE id=?",
            (f" Afgewezen via Telegram ({wanneer}).", challenger_id),
        )
        _sluit_portefeuilles(con, c)
        con.commit()
        return f"Challenger {challenger_id} afgewezen. Er is niets aan de instellingen veranderd."

    # akkoord
    if not droog:
        zet_in_config(config_pad, c["strategie"], c["parameter"], c["nieuwe_waarde"])
    toevoeging = " (droogtest, config.yaml niet aangepast)" if droog else ""
    con.execute(
        "UPDATE challengers SET status='aangenomen', uitslag=uitslag || ? WHERE id=?",
        (f" Aangenomen via Telegram ({wanneer}){toevoeging}.", challenger_id),
    )
    con.execute(
        """INSERT INTO wijzigingen (datum, strategie, parameter, oude_waarde, nieuwe_waarde,
                                    challenger_id, akkoord)
           VALUES (?,?,?,?,?,?,?)""",
        (datum, c["strategie"], c["parameter"], c["oude_waarde"], c["nieuwe_waarde"],
         challenger_id, f"Telegram, {wanneer}{toevoeging}"),
    )
    _sluit_portefeuilles(con, c)
    con.commit()
    if droog:
        return (
            f"Droogtest: akkoord ontvangen en verwerkt voor challenger {challenger_id}. In de "
            f"echte meting zou {c['strategie']}.{c['parameter']} nu van "
            f"{_mooi(c['oude_waarde'])} naar {_mooi(c['nieuwe_waarde'])} gaan. In de proefweek "
            f"is config.yaml NIET aangepast. De akkoordroute werkt."
        )
    return (
        f"Akkoord verwerkt. {c['strategie']}.{c['parameter']} staat nu op "
        f"{_mooi(c['nieuwe_waarde'])} (was {_mooi(c['oude_waarde'])}). Dat geldt vanaf de "
        f"ronde van {datum} en staat in de tabel 'wijzigingen'."
    )


def zet_in_config(pad: Path, strategie: str, parameter: str, waarde: float) -> None:
    """Pas een instelling aan in config.yaml, zonder de rest van het bestand te raken.

    We schrijven het bestand niet opnieuw uit met een yaml-bibliotheek, want dan
    verdwijnt alle uitleg in de commentaarregels. We vervangen alleen dat ene
    getal, en controleren daarna dat het bestand nog klopt.
    """
    import yaml

    regels = pad.read_text(encoding="utf-8").splitlines(keepends=True)
    in_strategieen = in_blok = False
    vervangen = False
    for i, regel in enumerate(regels):
        kaal = regel.rstrip("\n")
        if re.match(r"^strategieen:\s*(#.*)?$", kaal):
            in_strategieen = True
            continue
        if in_strategieen and re.match(r"^\S", kaal):
            break  # einde van het blok strategieen
        if in_strategieen and re.match(rf"^  {re.escape(strategie)}:\s*(#.*)?$", kaal):
            in_blok = True
            continue
        if in_blok and re.match(r"^  \S", kaal):
            break  # volgende strategie
        if in_blok:
            gevonden = re.match(rf"^(    {re.escape(parameter)}:\s*)([^\s#]+)(.*)$", kaal)
            if gevonden:
                nieuw = _mooi(waarde)
                regels[i] = f"{gevonden.group(1)}{nieuw}{gevonden.group(3)}\n"
                vervangen = True
                break
    if not vervangen:
        raise ValueError(f"{strategie}.{parameter} niet gevonden in {pad.name}")

    tekst = "".join(regels)
    controle = yaml.safe_load(tekst)
    if float(controle["strategieen"][strategie][parameter]) != float(waarde):
        raise ValueError("de aanpassing in config.yaml klopt niet; er is niets opgeslagen")
    pad.write_text(tekst, encoding="utf-8", newline="\n")


def nu_tekst() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
