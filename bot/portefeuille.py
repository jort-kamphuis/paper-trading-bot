"""De nep-broker.

Dit bestand rekent uit wat een trade met nep-geld zou hebben gekost en
opgeleverd. Er wordt nooit verbinding gemaakt met een broker of een beurs om
te handelen, en er is geen enkele functie die echt geld kan verplaatsen.
Zie tests/test_geen_echt_geld.py.

Alles rekent in dollars (USD). Aandelen en ETF's noteren in dollars en crypto
in USDT, wat we als dollar behandelen. Zo zit er geen wisselkoers tussen de
strategie en de meetlat: die zou meer ruis kunnen geven dan elk echt verschil.
Euro's zijn er alleen nog voor de weergave.

Hoeveel geld een aankoop krijgt, beslist de motor (10% van de waarde van de
portefeuille, zie motor.py). Hier wordt alleen gecontroleerd dat het kan.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Positie:
    symbool: str
    aantal: float
    kostprijs_usd: float  # alles wat erin ging, inclusief aankoopkosten
    gekocht_op: str
    koopprijs: float = 0.0  # de uitgevoerde prijs per stuk (met slippage)
    dividend_usd: float = 0.0  # ontvangen dividend sinds de aankoop
    koopreden: str = ""
    # Voor de AI: de laatste dag waarop de positie opnieuw is bevestigd. De
    # houdtijd telt vanaf hier. Zonder bevestiging is dat de aankoopdag.
    bevestigd_op: str | None = None


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
    eurusd: float  # alleen voor de weergave in euro's
    prijs_usd: float
    aantal: float
    bedrag_usd: float
    kosten_usd: float
    reden: str | None = None
    zekerheid: float | None = None
    resultaat_usd: float | None = None
    resultaat_pct: float | None = None


@dataclass
class Uitkering:
    """Een dividend of een splitsing, zoals die op een positie is verwerkt."""

    portefeuille: str
    symbool: str
    datum: str
    soort: str  # dividend of splitsing
    waarde: float  # dividend per stuk, of de factor van de splitsing
    bedrag_usd: float  # ontvangen dividend; 0 bij een splitsing


@dataclass
class Portefeuille:
    """Een portefeuille met nep-geld voor een strategie."""

    naam: str
    strategie: str
    start_kapitaal: float
    kas_usd: float
    kosten_pct: float
    slippage_pct: float
    positie_pct: float = 10.0
    # Hoeveel posities er tegelijk mogen zijn. None = geen grens (de meetlatten).
    max_posities: int | None = 10
    variant: str = "standaard"
    gestart_op: str | None = None
    posities: dict[str, Positie] = field(default_factory=dict)
    # Wat in deze run is gebeurd en nog niet is weggeschreven.
    nieuwe_trades: list[Trade] = field(default_factory=list)
    nieuwe_uitkeringen: list[Uitkering] = field(default_factory=list)

    # ---------- prijzen ----------

    @staticmethod
    def _prijs_usd(prijs_lokaal: float, valuta: str) -> float:
        """Alles noteert in dollars; een andere valuta kan niet en is een fout."""
        if valuta != "USD":
            raise ValueError(f"onverwachte valuta {valuta!r}; de bot rekent alleen in USD")
        return prijs_lokaal

    def _met_slippage(self, open_koers: float, actie: str) -> float:
        """Je krijgt nooit precies de openingskoers; dit maakt het ietsje slechter."""
        factor = self.slippage_pct / 100.0
        if actie == "KOOP":
            return open_koers * (1.0 + factor)
        return open_koers * (1.0 - factor)

    # ---------- handelen ----------

    def plek_vrij(self) -> bool:
        return self.max_posities is None or len(self.posities) < self.max_posities

    def ruimte_in_kas(self) -> float:
        """Het hoogste bedrag dat nu kan, met ruimte voor de kosten."""
        return max(0.0, self.kas_usd / (1.0 + self.kosten_pct / 100.0))

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
        bedrag_usd: float | None = None,
    ) -> Trade | None:
        """Koop voor 'bedrag_usd' (of minder als de kas niet genoeg is).

        Zonder bedrag: positie_pct van de kas plus wat er in posities is gestoken.
        De motor geeft altijd een bedrag mee; de standaard is er voor de tests.
        Geeft None als het niet kan: al in bezit, geen plek, of geen geld.
        """
        if symbool in self.posities or not self.plek_vrij():
            return None
        if bedrag_usd is None:
            ingelegd = sum(p.kostprijs_usd for p in self.posities.values())
            bedrag_usd = (self.kas_usd + ingelegd) * self.positie_pct / 100.0
        bedrag = min(bedrag_usd, self.ruimte_in_kas())
        if bedrag <= 1.0:
            return None

        prijs_lokaal = self._met_slippage(open_koers, "KOOP")
        prijs_usd = self._prijs_usd(prijs_lokaal, valuta)
        if prijs_usd <= 0:
            return None

        aantal = bedrag / prijs_usd
        kosten = bedrag * (self.kosten_pct / 100.0)
        self.kas_usd -= bedrag + kosten
        # Bij het inleggen van de hele kas blijft er rekenruis staan, soms
        # negatief. Dat is geen geld; weghalen, anders staat er '-0,00'.
        if abs(self.kas_usd) < 0.005:
            self.kas_usd = 0.0
        self.posities[symbool] = Positie(
            symbool=symbool,
            aantal=aantal,
            kostprijs_usd=bedrag + kosten,
            gekocht_op=datum,
            koopprijs=prijs_usd,
            koopreden=(reden or "")[:500],
            bevestigd_op=datum,
        )
        trade = Trade(
            portefeuille=self.naam, strategie=self.strategie, symbool=symbool, actie="KOOP",
            besluit_datum=besluit_datum, datum=datum, prijs_lokaal=prijs_lokaal, valuta=valuta,
            eurusd=eurusd, prijs_usd=prijs_usd, aantal=aantal, bedrag_usd=bedrag,
            kosten_usd=kosten, reden=reden, zekerheid=zekerheid,
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
        """Verkoop de hele positie. Het resultaat telt het ontvangen dividend mee."""
        positie = self.posities.get(symbool)
        if positie is None:
            return None

        prijs_lokaal = self._met_slippage(open_koers, "VERKOOP")
        prijs_usd = self._prijs_usd(prijs_lokaal, valuta)
        opbrengst = positie.aantal * prijs_usd
        kosten = opbrengst * (self.kosten_pct / 100.0)
        netto = opbrengst - kosten
        self.kas_usd += netto

        resultaat = netto + positie.dividend_usd - positie.kostprijs_usd
        resultaat_pct = 100.0 * resultaat / positie.kostprijs_usd if positie.kostprijs_usd else None
        trade = Trade(
            portefeuille=self.naam, strategie=self.strategie, symbool=symbool, actie="VERKOOP",
            besluit_datum=besluit_datum, datum=datum, prijs_lokaal=prijs_lokaal, valuta=valuta,
            eurusd=eurusd, prijs_usd=prijs_usd, aantal=positie.aantal, bedrag_usd=opbrengst,
            kosten_usd=kosten, reden=reden, zekerheid=zekerheid,
            resultaat_usd=resultaat, resultaat_pct=resultaat_pct,
        )
        self.nieuwe_trades.append(trade)
        del self.posities[symbool]
        return trade

    # ---------- dividend en splitsingen ----------

    def ontvang_dividend(self, symbool: str, per_stuk: float, datum: str) -> float:
        """Zet het dividend van een positie in de kas. Geeft het bedrag terug."""
        positie = self.posities.get(symbool)
        if positie is None or per_stuk <= 0:
            return 0.0
        bedrag = positie.aantal * per_stuk
        self.kas_usd += bedrag
        positie.dividend_usd += bedrag
        self.nieuwe_uitkeringen.append(
            Uitkering(self.naam, symbool, datum, "dividend", per_stuk, bedrag)
        )
        return bedrag

    def pas_splitsing_toe(self, symbool: str, factor: float, datum: str) -> None:
        """Na een splitsing heb je meer (of minder) stukken van een lagere (of hogere) prijs."""
        positie = self.posities.get(symbool)
        if positie is None or factor <= 0 or factor == 1:
            return
        positie.aantal *= factor
        positie.koopprijs /= factor
        self.nieuwe_uitkeringen.append(Uitkering(self.naam, symbool, datum, "splitsing", factor, 0.0))

    # ---------- waarde ----------

    def posities_waarde(self, prijzen_usd: dict[str, float]) -> float:
        """Wat de open posities waard zijn tegen de meegegeven slotkoersen."""
        totaal = 0.0
        for symbool, positie in self.posities.items():
            prijs = prijzen_usd.get(symbool)
            if prijs is None:
                # Geen koers bekend (zou niet moeten kunnen; de motor vult gaten
                # met de laatste koers). Reken met de kostprijs, zodat de waarde
                # niet stilletjes wegvalt.
                totaal += positie.kostprijs_usd
            else:
                totaal += positie.aantal * prijs
        return totaal

    def totale_waarde(self, prijzen_usd: dict[str, float]) -> float:
        return self.kas_usd + self.posities_waarde(prijzen_usd)

    # ---------- opslaan en inlezen ----------

    def bewaar(self, con: sqlite3.Connection) -> None:
        """Schrijf de stand, de nieuwe trades en uitkeringen naar de database.

        Let op: hier wordt niet vastgelegd (geen commit). De motor doet dat
        een keer per dag, zodat alles van die dag in een keer doorgaat of in
        een keer niet.
        """
        con.execute(
            """INSERT INTO portefeuilles (naam, strategie, variant, start_kapitaal, kas_usd, gestart_op, actief)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(naam) DO UPDATE SET kas_usd=excluded.kas_usd""",
            (self.naam, self.strategie, self.variant, self.start_kapitaal, self.kas_usd, self.gestart_op),
        )
        con.execute("DELETE FROM posities WHERE portefeuille = ?", (self.naam,))
        if self.posities:
            con.executemany(
                """INSERT INTO posities (portefeuille, symbool, aantal, kostprijs_usd, gekocht_op,
                                         koopprijs, dividend_usd, koopreden, bevestigd_op)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (self.naam, p.symbool, p.aantal, p.kostprijs_usd, p.gekocht_op,
                     p.koopprijs, p.dividend_usd, p.koopreden, p.bevestigd_op)
                    for p in self.posities.values()
                ],
            )
        if self.nieuwe_trades:
            con.executemany(
                """INSERT INTO trades (portefeuille, strategie, symbool, actie, besluit_datum,
                                       datum, prijs_lokaal, valuta, eurusd, prijs_usd, aantal,
                                       bedrag_usd, kosten_usd, reden, zekerheid,
                                       resultaat_usd, resultaat_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        t.portefeuille, t.strategie, t.symbool, t.actie, t.besluit_datum,
                        t.datum, t.prijs_lokaal, t.valuta, t.eurusd, t.prijs_usd, t.aantal,
                        t.bedrag_usd, t.kosten_usd, t.reden, t.zekerheid,
                        t.resultaat_usd, t.resultaat_pct,
                    )
                    for t in self.nieuwe_trades
                ],
            )
            self.nieuwe_trades.clear()
        if self.nieuwe_uitkeringen:
            con.executemany(
                """INSERT INTO uitkeringen (portefeuille, symbool, datum, soort, waarde, bedrag_usd)
                   VALUES (?,?,?,?,?,?)""",
                [(u.portefeuille, u.symbool, u.datum, u.soort, u.waarde, u.bedrag_usd)
                 for u in self.nieuwe_uitkeringen],
            )
            self.nieuwe_uitkeringen.clear()

    def bewaar_waarde(self, con: sqlite3.Connection, datum: str, prijzen_usd: dict[str, float]) -> None:
        posities_usd = self.posities_waarde(prijzen_usd)
        con.execute(
            """INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd)
               VALUES (?,?,?,?,?)
               ON CONFLICT(portefeuille, datum) DO UPDATE SET
                 kas_usd=excluded.kas_usd, posities_usd=excluded.posities_usd,
                 totaal_usd=excluded.totaal_usd""",
            (self.naam, datum, self.kas_usd, posities_usd, self.kas_usd + posities_usd),
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
            kas_usd=rij["kas_usd"],
            variant=rij["variant"],
            gestart_op=rij["gestart_op"],
            kosten_pct=float(instellingen["kosten_pct"]),
            slippage_pct=float(instellingen["slippage_pct"]),
            positie_pct=float(instellingen.get("positie_pct", 10.0)),
            max_posities=instellingen.get("max_posities", 10),
        )
        for p in con.execute("SELECT * FROM posities WHERE portefeuille = ?", (naam,)):
            pf.posities[p["symbool"]] = Positie(
                symbool=p["symbool"],
                aantal=p["aantal"],
                kostprijs_usd=p["kostprijs_usd"],
                gekocht_op=p["gekocht_op"],
                koopprijs=p["koopprijs"] or 0.0,
                dividend_usd=p["dividend_usd"] or 0.0,
                koopreden=p["koopreden"] or "",
                bevestigd_op=p["bevestigd_op"] or p["gekocht_op"],
            )
        return pf


def nieuwe_portefeuille(
    naam: str, strategie: str, instellingen: dict, gestart_op: str | None = None,
    variant: str = "standaard",
) -> Portefeuille:
    start = float(instellingen["start_kapitaal_usd"])
    return Portefeuille(
        naam=naam,
        strategie=strategie,
        start_kapitaal=start,
        kas_usd=start,
        kosten_pct=float(instellingen["kosten_pct"]),
        slippage_pct=float(instellingen["slippage_pct"]),
        positie_pct=float(instellingen.get("positie_pct", 10.0)),
        max_posities=instellingen.get("max_posities", 10),
        variant=variant,
        gestart_op=gestart_op,
    )
