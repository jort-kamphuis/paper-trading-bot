"""Controleert of de dagelijkse ronde het echte leven aankan.

Het dagelijkse script stopt na elke run en start de volgende dag opnieuw.
Daartussen mag niets verloren gaan. En als GitHub het script per ongeluk
twee keer start, mogen er geen dubbele trades ontstaan.
"""

from __future__ import annotations

import pytest

from bot import database
from bot.motor import Motor
from bot.strategieen.basis import Order, Strategie
from tests.conftest import maak_markt, werkdagen


class KooptOpEenDag(Strategie):
    """Koopt een keer, op de opgegeven dag."""

    naam = "test_strategie"

    def __init__(self, koopdatum: str):
        super().__init__({})
        self.koopdatum = koopdatum
        self.aantal_keer_gevraagd = 0

    def besluit(self, datum, markt, symbolen, posities):
        self.aantal_keer_gevraagd += 1
        if datum == self.koopdatum and not posities:
            return [Order(symbool="AAA", actie="KOOP", reden="test")]
        return []


class Struikelt(Strategie):
    """Gaat onderuit op een bepaalde dag, om een storing na te bootsen."""

    naam = "struikelaar"

    def __init__(self, valdatum: str):
        super().__init__({})
        self.valdatum = valdatum

    def besluit(self, datum, markt, symbolen, posities):
        if datum == self.valdatum:
            raise RuntimeError("nagebootste storing")
        return []


def _markt(dagen: int = 6):
    datums = werkdagen(dagen)
    return maak_markt({"AAA": [100.0] * dagen}, datums), datums


def test_twee_keer_dezelfde_dag_geeft_geen_dubbele_trades(con, cfg_min, vaste_wisselkoers):
    markt, datums = _markt()

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[1]), ["AAA"])
    motor.draai_periode(datums[0], datums[3])
    na_een_keer = con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
    assert na_een_keer == 1

    # Nog een keer draaien over precies dezelfde dagen, met een verse motor,
    # alsof GitHub het script twee keer startte.
    motor2 = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor2.voeg_toe(KooptOpEenDag(datums[1]), ["AAA"])
    motor2.draai_periode(datums[0], datums[3])

    na_twee_keer = con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
    assert na_twee_keer == 1, "de tweede run heeft een dubbele trade gemaakt"
    assert motor2.overgeslagen_dagen == 4


def test_de_kas_blijft_gelijk_bij_een_tweede_run(con, cfg_min, vaste_wisselkoers):
    markt, datums = _markt()
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[1]), ["AAA"])
    motor.draai_periode(datums[0], datums[3])
    kas_na_een = con.execute(
        "SELECT kas_eur FROM portefeuilles WHERE naam='test_strategie'"
    ).fetchone()["kas_eur"]

    motor2 = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor2.voeg_toe(KooptOpEenDag(datums[1]), ["AAA"])
    motor2.draai_periode(datums[0], datums[3])
    kas_na_twee = con.execute(
        "SELECT kas_eur FROM portefeuilles WHERE naam='test_strategie'"
    ).fetchone()["kas_eur"]

    assert kas_na_twee == pytest.approx(kas_na_een)


def test_wachtrij_overleeft_het_stoppen_van_het_script(con, cfg_min, vaste_wisselkoers):
    """Besluit van gisteren, uitvoeren vandaag, met het script ertussen gestopt."""
    markt, datums = _markt()

    # Dag 1: de strategie besluit te kopen. Het script stopt daarna.
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.draai_dag(datums[0])
    del motor

    wachtrij = con.execute("SELECT * FROM wachtrij").fetchall()
    assert len(wachtrij) == 1, "het besluit moet in de database staan, niet in het geheugen"
    assert wachtrij[0]["symbool"] == "AAA"
    assert wachtrij[0]["besluit_datum"] == datums[0]
    assert con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0

    # Dag 2: een volledig nieuwe motor, zoals na een herstart.
    motor2 = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor2.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor2.draai_dag(datums[1])

    trades = con.execute("SELECT * FROM trades").fetchall()
    assert len(trades) == 1, "de order van gisteren is niet uitgevoerd"
    assert trades[0]["besluit_datum"] == datums[0]
    assert trades[0]["datum"] == datums[1]
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0


def test_gemiste_dagen_worden_ingehaald(con, cfg_min, vaste_wisselkoers):
    """Een paar dagen niets gedraaid: die worden alsnog verwerkt."""
    markt, datums = _markt(8)

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.draai_dag(datums[0])

    # Drie dagen niets, daarna alles in een keer inhalen.
    motor2 = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor2.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor2.draai_periode(datums[0], datums[4])

    dagen = con.execute(
        "SELECT COUNT(*) n FROM verwerkte_dagen WHERE portefeuille='test_strategie'"
    ).fetchone()["n"]
    assert dagen == 5
    waardes = con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"]
    assert waardes == 5


def test_verwerkte_dagen_worden_bijgehouden(con, cfg_min, vaste_wisselkoers):
    markt, datums = _markt()
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag("nooit"), ["AAA"])
    motor.draai_periode(datums[0], datums[2])

    assert database.dag_is_verwerkt(con, "test_strategie", datums[0])
    assert database.dag_is_verwerkt(con, "test_strategie", datums[2])
    assert not database.dag_is_verwerkt(con, "test_strategie", datums[4])
    assert database.laatste_verwerkte_dag(con, "test_strategie") == datums[2]


def test_een_storing_laat_geen_halve_dag_achter(con, cfg_min, vaste_wisselkoers):
    """Gaat het halverwege een dag mis, dan moet die hele dag teruggedraaid zijn."""
    markt, datums = _markt()

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    # De eerste strategie doet zijn werk, de tweede gaat onderuit op dag 2.
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.voeg_toe(Struikelt(datums[1]), ["AAA"])

    motor.draai_dag(datums[0])
    waardes_voor = con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"]

    with pytest.raises(RuntimeError):
        motor.draai_dag(datums[1])

    waardes_na = con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"]
    assert waardes_na == waardes_voor, "er is een halve dag blijven staan"
    assert not database.dag_is_verwerkt(con, "test_strategie", datums[1])
    assert con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0


def test_na_een_storing_klopt_ook_het_geheugen_weer(con, cfg_min, vaste_wisselkoers):
    """Na terugdraaien moet de portefeuille in het geheugen gelijk zijn aan de database."""
    markt, datums = _markt()

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    # Deze koopt op dag 2, maar de tweede strategie gaat die dag onderuit.
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.voeg_toe(Struikelt(datums[1]), ["AAA"])

    motor.draai_dag(datums[0])
    kas_voor = motor.deelnemers[0].portefeuille.kas_eur

    with pytest.raises(RuntimeError):
        motor.draai_dag(datums[1])

    # De aankoop van dag 2 is teruggedraaid, dus de kas moet weer de oude zijn.
    pf = motor.deelnemers[0].portefeuille
    assert pf.kas_eur == pytest.approx(kas_voor), "het geheugen loopt uit de pas"
    assert pf.posities == {}, "er staat een positie in het geheugen die niet in de database zit"
    assert pf.nieuwe_trades == []
    # En de order van gisteren staat nog netjes klaar.
    assert len(motor.deelnemers[0].wachtrij) == 1

    uit_db = con.execute(
        "SELECT kas_eur FROM portefeuilles WHERE naam='test_strategie'"
    ).fetchone()["kas_eur"]
    assert pf.kas_eur == pytest.approx(uit_db)


class KooptAlles(Strategie):
    """Wil elk symbool kopen dat hij nog niet heeft."""

    naam = "koopt_alles"

    def besluit(self, datum, markt, symbolen, posities):
        return [
            Order(symbool=s, actie="KOOP", reden=f"test {datum}")
            for s in symbolen
            if s not in posities and markt.bar(s, datum) is not None
        ]


def test_alleen_de_uitgevoerde_order_verdwijnt_uit_de_wachtrij(
    con, cfg_min, vaste_wisselkoers
):
    """Een order wegstrepen mag nooit een andere order raken."""
    datums = werkdagen(4)
    # AAA handelt elke dag, ZZZ alleen op de laatste dag.
    import pandas as pd

    df_a = pd.DataFrame(
        {"open": [100.0] * 4, "hoog": [100.0] * 4, "laag": [100.0] * 4,
         "slot": [100.0] * 4, "volume": [1.0] * 4, "valuta": ["USD"] * 4},
        index=datums,
    )
    df_a.index.name = "datum"
    df_z = pd.DataFrame(
        {"open": [50.0], "hoog": [50.0], "laag": [50.0], "slot": [50.0],
         "volume": [1.0], "valuta": ["USD"]},
        index=[datums[3]],
    )
    df_z.index.name = "datum"
    from bot.markt import Markt

    markt = Markt({"AAA": df_a, "ZZZ": df_z})

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptAlles({}), ["AAA", "ZZZ"])

    motor.draai_dag(datums[0])  # besluit: koop AAA (ZZZ handelt nog niet)
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 1

    motor.draai_dag(datums[1])  # AAA wordt gekocht en verdwijnt uit de rij
    symbolen_in_rij = [r["symbool"] for r in con.execute("SELECT symbool FROM wachtrij")]
    assert "AAA" not in symbolen_in_rij
    gekocht = [r["symbool"] for r in con.execute("SELECT symbool FROM trades")]
    assert gekocht == ["AAA"]

    motor.draai_dag(datums[3])  # nu bestaat ZZZ en komt die in de rij
    rij = con.execute("SELECT symbool, besluit_datum FROM wachtrij").fetchall()
    assert [r["symbool"] for r in rij] == ["ZZZ"]
    assert rij[0]["besluit_datum"] == datums[3]


def test_wachtrij_in_database_hoort_bij_de_juiste_portefeuille(
    con, cfg_min, vaste_wisselkoers
):
    """Twee strategieën delen de tabel; hun orders mogen niet door elkaar lopen."""
    markt, datums = _markt()
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    een = KooptOpEenDag(datums[0])
    een.naam = "strategie_een"
    twee = KooptOpEenDag(datums[0])
    twee.naam = "strategie_twee"
    motor.voeg_toe(een, ["AAA"])
    motor.voeg_toe(twee, ["AAA"])

    motor.draai_dag(datums[0])
    rijen = con.execute("SELECT portefeuille FROM wachtrij ORDER BY portefeuille").fetchall()
    assert [r["portefeuille"] for r in rijen] == ["strategie_een", "strategie_twee"]

    motor.draai_dag(datums[1])
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0
    trades = con.execute(
        "SELECT portefeuille FROM trades ORDER BY portefeuille"
    ).fetchall()
    assert [t["portefeuille"] for t in trades] == ["strategie_een", "strategie_twee"]


def test_later_gestarte_portefeuille_doet_niet_met_terugwerkende_kracht_mee(
    con, cfg_min, vaste_wisselkoers
):
    """Een challenger die later start, mag niet over eerdere dagen meebeslissen."""
    markt, datums = _markt()
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    vroeg = KooptOpEenDag(datums[0])
    vroeg.naam = "vroeg"
    laat = KooptOpEenDag(datums[0])
    laat.naam = "laat"
    motor.voeg_toe(vroeg, ["AAA"], gestart_op=datums[0])
    motor.voeg_toe(laat, ["AAA"], gestart_op=datums[3])
    motor.draai_periode(datums[0], datums[-1])

    eerste_dag_laat = con.execute(
        "SELECT MIN(datum) d FROM waarde WHERE portefeuille='laat'"
    ).fetchone()["d"]
    assert eerste_dag_laat == datums[3]
    trades_laat = con.execute("SELECT COUNT(*) n FROM trades WHERE portefeuille='laat'").fetchone()["n"]
    assert trades_laat == 0, "de late portefeuille kocht op een dag van voor zijn start"


def test_startdatum_wordt_onthouden(con):
    assert database.lees_instelling(con, "start_datum") is None
    database.zet_instelling(con, "start_datum", "2026-11-13")
    assert database.lees_instelling(con, "start_datum") == "2026-11-13"
    # Opnieuw zetten overschrijft, maakt geen tweede regel.
    database.zet_instelling(con, "start_datum", "2026-11-14")
    assert database.lees_instelling(con, "start_datum") == "2026-11-14"
    assert con.execute("SELECT COUNT(*) n FROM instellingen").fetchone()["n"] == 1


def test_portefeuille_wordt_niet_opnieuw_aangemaakt(con, cfg_min, vaste_wisselkoers):
    """Bij de tweede run moet de bestaande portefeuille worden opgepakt."""
    markt, datums = _markt()

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.draai_periode(datums[0], datums[2])
    kas = motor.deelnemers[0].portefeuille.kas_eur
    assert kas < 1000.0  # er is gekocht

    motor2 = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor2.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    assert motor2.deelnemers[0].portefeuille.kas_eur == pytest.approx(kas)
    assert "AAA" in motor2.deelnemers[0].portefeuille.posities
    assert con.execute("SELECT COUNT(*) n FROM portefeuilles").fetchone()["n"] == 1


def test_overzicht_toont_de_stand(con, cfg_min, vaste_wisselkoers):
    from bot import overzicht

    markt, datums = _markt()
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KooptOpEenDag(datums[0]), ["AAA"])
    motor.draai_periode(datums[0], datums[2])

    standen = overzicht.portefeuille_standen(con)
    assert len(standen) == 1
    assert standen[0]["portefeuille"] == "test_strategie"

    bericht = overzicht.dagbericht(con, datums[1])
    assert "test_strategie" in bericht
    assert "KOOP" in bericht
    assert "AAA" in bericht
