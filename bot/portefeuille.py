"""De nep-broker.

Dit bestand rekent uit wat een trade met nep-geld zou hebben gekost en
opgeleverd. Er wordt nooit verbinding gemaakt met een broker of een beurs om
te handelen, en er is geen enkele functie die echt geld kan verplaatsen.
Zie tests/test_geen_echt_geld.py.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Positie:
    symbool: str
    aantal: float
    kostprijs_eur: float  # alles wat erin ging, inclusief aankoopkosten
    gekocht_op: str

    def dagen_vast(self, beursdagen_sinds: int) -> int:
        return beursdagen_sinds


@dataclass
class Trade:
    """Een uitgevoerde trade, klaar om in het logboek te zetten."""

    portefeuille: str
    strategie: str
    symbool: str
    actie: str  # KOOP of VERKOOP
    besluit_datum: str
    datum: str
    prijs_lokaal: float
    valuta: str
    eurusd: float
    prijs_eur: float
    aantal: float
    bedrag_eur: float
    kosten_eur: float
    reden: str | None = None
    zekerheid: float | None = None
    resultaat_eur: float | None = None
    resultaat_pct: float | None = None


@dataclass
class Portefeuille:
    """Een portefeuille met nep-geld voor een strategie."""

    naam: str
    strategie: str
    start_kapitaal: float
    kas_eur: float
    max_per_trade_eur: float
    kosten_pct: float
    slippage_pct: float
    variant: str = "standaard"
    gestart_op: str | None = None
    posities: dict[str, Positie] = field(default_factory=dict)
    # Trades die in deze run zijn gedaan en nog niet zijn weggeschreven.
    nieuwe_trades: list[Trade] = field(default_factory=list)

    # ---------- prijzen ----------

    def _prijs_eur(self, prijs_lokaal: float, valuta: str, eurusd: float) -> float:
        """Reken een koers om naar euro's."""
        if valuta == "EUR":
            return prijs_lokaal
        if eurusd <= 0:
            raise ValueError(f"onmogelijke wisselkoers: {eurusd}")
        return prijs_lokaal / eurusd

    def _met_slippage(self, open_koers: float, actie: str) -> float:
        """Je krijgt nooit precies de openingskoers; dit maakt het ietsje slechter."""
        factor = self.slippage_pct / 100.0
        if actie == "KOOP":
            return open_koers * (1.0 + factor)
        return open_koers * (1.0 - factor)

    # ---------- handelen ----------

    def kan_kopen(self, symbool: str) -> bool:
        """Kopen mag als we het symbool nog niet hebben en er genoeg kas is."""
        if symbool in self.posities:
            return False
        return self.beschikbaar_bedrag() > 1.0  # minder dan een euro is zinloos

    def beschikbaar_bedrag(self) -> float:
        """Hoeveel we deze trade maximaal inleggen, inclusief ruimte voor de kosten."""
        ruimte = self.kas_eur / (1.0 + self.kosten_pct / 100.0)
        return max(0.0, min(self.max_per_trade_eur, ruimte))

    def koop(
        self,
        symbool: str,
        datum: str,
        besluit_datum: str,
        open_koers: float,
        valuta: str,
        eurusd: float,
        reden: str | None = None,
        zekerheid: float | None = None,
        bedrag_eur: float | None = None,
    ) -> Trade | None:
        """Koop voor maximaal max_per_trade_eur. Geeft None als het niet kan."""
        if symbool in self.posities:
            return None
        bedrag = self.beschikbaar_bedrag() if bedrag_eur is None else bedrag_eur
        bedrag = min(bedrag, self.kas_eur / (1.0 + self.kosten_pct / 100.0))
        if bedrag <= 1.0:
            return None

        prijs_lokaal = self._met_slippage(open_koers, "KOOP")
        prijs_eur = self._prijs_eur(prijs_lokaal, valuta, eurusd)
        if prijs_eur <= 0:
            return None

        aantal = bedrag / prijs_eur
        kosten = bedrag * (self.kosten_pct / 100.0)
        self.kas_eur -= bedrag + kosten
        self.posities[symbool] = Positie(
            symbool=symbool,
            aantal=aantal,
            kostprijs_eur=bedrag + kosten,
            gekocht_op=datum,
        )
        trade = Trade(
            portefeuille=self.naam,
            strategie=self.strategie,
            symbool=symbool,
            actie="KOOP",
            besluit_datum=besluit_datum,
            datum=datum,
            prijs_lokaal=prijs_lokaal,
            valuta=valuta,
            eurusd=eurusd,
            prijs_eur=prijs_eur,
            aantal=aantal,
            bedrag_eur=bedrag,
            kosten_eur=kosten,
            reden=reden,
            zekerheid=zekerheid,
        )
        self.nieuwe_trades.append(trade)
        return trade

    def verkoop(
        self,
        symbool: str,
        datum: str,
        besluit_datum: str,
        open_koers: float,
        valuta: str,
        eurusd: float,
        reden: str | None = None,
        zekerheid: float | None = None,
    ) -> Trade | None:
        """Verkoop de hele positie. Geeft None als we die niet hebben."""
        positie = self.posities.get(symbool)
        if positie is None:
            return None

        prijs_lokaal = self._met_slippage(open_koers, "VERKOOP")
        prijs_eur = self._prijs_eur(prijs_lokaal, valuta, eurusd)
        opbrengst = positie.aantal * prijs_eur
        kosten = opbrengst * (self.kosten_pct / 100.0)
        netto = opbrengst - kosten
        self.kas_eur += netto

        resultaat = netto - positie.kostprijs_eur
        resultaat_pct = (
            100.0 * resultaat / positie.kostprijs_eur if positie.kostprijs_eur else None
        )
        trade = Trade(
            portefeuille=self.naam,
            strategie=self.strategie,
            symbool=symbool,
            actie="VERKOOP",
            besluit_datum=besluit_datum,
            datum=datum,
            prijs_lokaal=prijs_lokaal,
            valuta=valuta,
            eurusd=eurusd,
            prijs_eur=prijs_eur,
            aantal=positie.aantal,
            bedrag_eur=opbrengst,
            kosten_eur=kosten,
            reden=reden,
            zekerheid=zekerheid,
            resultaat_eur=resultaat,
            resultaat_pct=resultaat_pct,
        )
        self.nieuwe_trades.append(trade)
        del self.posities[symbool]
        return trade

    # ---------- waarde ----------

    def posities_waarde(self, prijzen_eur: dict[str, float]) -> float:
        """Wat de open posities waard zijn tegen de meegegeven slotkoersen."""
        totaal = 0.0
        for symbool, positie in self.posities.items():
            prijs = prijzen_eur.get(symbool)
            if prijs is None:
                # Geen koers vandaag (beurs dicht): reken met de kostprijs,
                # zodat de waarde niet stilletjes wegvalt.
                totaal += positie.kostprijs_eur
            else:
                totaal += positie.aantal * prijs
        return totaal

    def totale_waarde(self, prijzen_eur: dict[str, float]) -> float:
        return self.kas_eur + self.posities_waarde(prijzen_eur)

    # ---------- opslaan en inlezen ----------

    def bewaar(self, con: sqlite3.Connection) -> None:
        """Schrijf de stand en de nieuwe trades naar de database."""
        con.execute(
            """INSERT INTO portefeuilles (naam, strategie, variant, start_kapitaal, kas_eur, gestart_op, actief)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(naam) DO UPDATE SET kas_eur=excluded.kas_eur""",
            (
                self.naam,
                self.strategie,
                self.variant,
                self.start_kapitaal,
                self.kas_eur,
                self.gestart_op,
            ),
        )
        con.execute("DELETE FROM posities WHERE portefeuille = ?", (self.naam,))
        if self.posities:
            con.executemany(
                """INSERT INTO posities (portefeuille, symbool, aantal, kostprijs_eur, gekocht_op)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (self.naam, p.symbool, p.aantal, p.kostprijs_eur, p.gekocht_op)
                    for p in self.posities.values()
                ],
            )
        if self.nieuwe_trades:
            con.executemany(
                """INSERT INTO trades (portefeuille, strategie, symbool, actie, besluit_datum,
                                       datum, prijs_lokaal, valuta, eurusd, prijs_eur, aantal,
                                       bedrag_eur, kosten_eur, reden, zekerheid,
                                       resultaat_eur, resultaat_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        t.portefeuille, t.strategie, t.symbool, t.actie, t.besluit_datum,
                        t.datum, t.prijs_lokaal, t.valuta, t.eurusd, t.prijs_eur, t.aantal,
                        t.bedrag_eur, t.kosten_eur, t.reden, t.zekerheid,
                        t.resultaat_eur, t.resultaat_pct,
                    )
                    for t in self.nieuwe_trades
                ],
            )
            self.nieuwe_trades.clear()
        con.commit()

    def bewaar_waarde(self, con: sqlite3.Connection, datum: str, prijzen_eur: dict[str, float]) -> None:
        posities_eur = self.posities_waarde(prijzen_eur)
        con.execute(
            """INSERT INTO waarde (portefeuille, datum, kas_eur, posities_eur, totaal_eur)
               VALUES (?,?,?,?,?)
               ON CONFLICT(portefeuille, datum) DO UPDATE SET
                 kas_eur=excluded.kas_eur, posities_eur=excluded.posities_eur,
                 totaal_eur=excluded.totaal_eur""",
            (self.naam, datum, self.kas_eur, posities_eur, self.kas_eur + posities_eur),
        )

    @classmethod
    def uit_database(cls, con: sqlite3.Connection, naam: str, instellingen: dict) -> "Portefeuille | None":
        rij = con.execute("SELECT * FROM portefeuilles WHERE naam = ?", (naam,)).fetchone()
        if rij is None:
            return None
        pf = cls(
            naam=rij["naam"],
            strategie=rij["strategie"],
            start_kapitaal=rij["start_kapitaal"],
            kas_eur=rij["kas_eur"],
            variant=rij["variant"],
            gestart_op=rij["gestart_op"],
            max_per_trade_eur=instellingen["max_per_trade_eur"],
            kosten_pct=instellingen["kosten_pct"],
            slippage_pct=instellingen["slippage_pct"],
        )
        for p in con.execute("SELECT * FROM posities WHERE portefeuille = ?", (naam,)):
            pf.posities[p["symbool"]] = Positie(
                symbool=p["symbool"],
                aantal=p["aantal"],
                kostprijs_eur=p["kostprijs_eur"],
                gekocht_op=p["gekocht_op"],
            )
        return pf


def nieuwe_portefeuille(
    naam: str, strategie: str, instellingen: dict, gestart_op: str | None = None,
    variant: str = "standaard",
) -> Portefeuille:
    start = float(instellingen["start_kapitaal_eur"])
    return Portefeuille(
        naam=naam,
        strategie=strategie,
        start_kapitaal=start,
        kas_eur=start,
        max_per_trade_eur=float(instellingen["max_per_trade_eur"]),
        kosten_pct=float(instellingen["kosten_pct"]),
        slippage_pct=float(instellingen["slippage_pct"]),
        variant=variant,
        gestart_op=gestart_op,
    )
