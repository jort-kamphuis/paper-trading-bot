"""De motor: laat de strategieën dag na dag hun werk doen.

De regel die alles eerlijk houdt: een strategie beslist op basis van de data
tot en met dag T, en de trade wordt uitgevoerd op de openingskoers van de
eerstvolgende handelsdag. Zo kan geen enkele strategie een koers gebruiken die
op het moment van beslissen nog niet bekend was.

Dezelfde motor draait de backtest en de echte dagelijkse ronde. Dat is met
opzet: zo kan de backtest niet per ongeluk andere regels hanteren.

Twee dingen maken hem bestand tegen het echte leven:
* De wachtrij met orders staat in de database. Het dagelijkse script stopt
  tussen gisteren en vandaag, en een besluit van gisteren moet dat overleven.
* Een dag die al verwerkt is, wordt overgeslagen. Draait het script per
  ongeluk twee keer, dan komen er geen dubbele trades.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from bot import database
from bot.markt import Markt
from bot.portefeuille import Portefeuille, nieuwe_portefeuille
from bot.strategieen.basis import Order, Strategie

# Een order die na zoveel kalenderdagen nog niet is uitgevoerd, vervalt.
# Zonder deze grens zou een order uit het verre verleden ineens kunnen afgaan.
ORDER_VERVALT_NA_DAGEN = 5


@dataclass
class WachtendeOrder:
    order: Order
    besluit_datum: str


@dataclass
class Deelnemer:
    """Een strategie met zijn portefeuille en de symbolen die hij volgt."""

    strategie: Strategie
    portefeuille: Portefeuille
    symbolen: list[str]
    wachtrij: list[WachtendeOrder] = field(default_factory=list)


class Motor:
    def __init__(self, con: sqlite3.Connection, cfg: dict, markt: Markt, wisselkoers):
        self.con = con
        self.cfg = cfg
        self.markt = markt
        self.wisselkoers = wisselkoers
        self.deelnemers: list[Deelnemer] = []
        self.overgeslagen_dagen = 0

    # ---------- opzetten ----------

    def voeg_toe(
        self, strategie: Strategie, symbolen: list[str], gestart_op: str | None = None
    ) -> Deelnemer:
        """Zet een strategie klaar met zijn portefeuille en zijn wachtrij."""
        instellingen = self.cfg["algemeen"]
        naam = strategie.naam
        pf = Portefeuille.uit_database(self.con, naam, instellingen)
        if pf is None:
            pf = nieuwe_portefeuille(naam, strategie.naam, instellingen, gestart_op)
            pf.bewaar(self.con)
            self.con.commit()
        deelnemer = Deelnemer(strategie=strategie, portefeuille=pf, symbolen=list(symbolen))
        deelnemer.wachtrij = self._lees_wachtrij(naam)
        self.deelnemers.append(deelnemer)
        return deelnemer

    # ---------- wachtrij in de database ----------

    def _lees_wachtrij(self, portefeuille: str) -> list[WachtendeOrder]:
        rijen = self.con.execute(
            "SELECT * FROM wachtrij WHERE portefeuille = ? ORDER BY id", (portefeuille,)
        ).fetchall()
        return [
            WachtendeOrder(
                order=Order(
                    symbool=r["symbool"],
                    actie=r["actie"],
                    reden=r["reden"] or "",
                    zekerheid=r["zekerheid"],
                ),
                besluit_datum=r["besluit_datum"],
            )
            for r in rijen
        ]

    def _zet_in_wachtrij(self, portefeuille: str, wachtend: WachtendeOrder) -> None:
        self.con.execute(
            """INSERT INTO wachtrij (portefeuille, symbool, actie, reden, zekerheid, besluit_datum)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(portefeuille, symbool, actie, besluit_datum) DO NOTHING""",
            (
                portefeuille,
                wachtend.order.symbool,
                wachtend.order.actie,
                wachtend.order.reden,
                wachtend.order.zekerheid,
                wachtend.besluit_datum,
            ),
        )

    def _haal_uit_wachtrij(self, portefeuille: str, wachtend: WachtendeOrder) -> None:
        """Haal een order weg op zijn eigen kenmerken, niet op een rijnummer.

        Die vier kolommen zijn samen uniek. Een rijnummer van de database zou
        hier gevaarlijk zijn: als een invoegpoging niets doet omdat de order al
        bestond, geeft de database het nummer van een eerdere rij terug, en dan
        zou je de verkeerde order wissen.
        """
        self.con.execute(
            """DELETE FROM wachtrij
               WHERE portefeuille = ? AND symbool = ? AND actie = ? AND besluit_datum = ?""",
            (portefeuille, wachtend.order.symbool, wachtend.order.actie, wachtend.besluit_datum),
        )

    # ---------- een dag draaien ----------

    def draai_dag(self, datum: str, stil: bool = True) -> None:
        """Doe alles voor een dag: eerst uitvoeren, dan waarderen, dan beslissen.

        Alles van deze dag gaat in een keer de database in. Gaat er iets mis,
        dan is er niets half gedaan.
        """
        koersen_eur = self.markt.slotkoersen_eur(datum, self.wisselkoers)

        try:
            for deelnemer in self.deelnemers:
                naam = deelnemer.portefeuille.naam
                if database.dag_is_verwerkt(self.con, naam, datum):
                    self.overgeslagen_dagen += 1
                    continue
                self._voer_wachtrij_uit(deelnemer, datum, stil=stil)
                deelnemer.portefeuille.bewaar_waarde(self.con, datum, koersen_eur)
                self._beslis(deelnemer, datum)
                database.markeer_dag_verwerkt(self.con, naam, datum)
            self.con.commit()
        except Exception:
            # De database gaat terug naar het begin van de dag. De
            # portefeuilles in het geheugen weten daar niets van, dus die
            # lezen we opnieuw in. Anders zou een strategie die vandaag al
            # "gekocht" had met een verkeerde kas verder rekenen.
            self.con.rollback()
            self._herstel_na_storing()
            raise

    def _herstel_na_storing(self) -> None:
        """Lees alle portefeuilles en wachtrijen opnieuw uit de database."""
        instellingen = self.cfg["algemeen"]
        for deelnemer in self.deelnemers:
            naam = deelnemer.portefeuille.naam
            opnieuw = Portefeuille.uit_database(self.con, naam, instellingen)
            if opnieuw is not None:
                deelnemer.portefeuille = opnieuw
            else:
                deelnemer.portefeuille.nieuwe_trades.clear()
            deelnemer.wachtrij = self._lees_wachtrij(naam)

    def _voer_wachtrij_uit(self, deelnemer: Deelnemer, datum: str, stil: bool) -> None:
        """Voer de orders uit die vandaag aan de beurt zijn."""
        pf = deelnemer.portefeuille
        eurusd = self.wisselkoers.op(datum)
        overblijvend: list[WachtendeOrder] = []

        # Verkopen eerst, zodat het geld weer beschikbaar is om te kopen.
        wachtrij = sorted(
            deelnemer.wachtrij, key=lambda w: 0 if w.order.actie == "VERKOOP" else 1
        )

        for wachtend in wachtrij:
            order = wachtend.order
            if datum <= wachtend.besluit_datum:
                overblijvend.append(wachtend)
                continue

            if self._order_verlopen(wachtend, datum):
                if not stil:
                    print(f"    order vervallen: {order.actie} {order.symbool}")
                self._haal_uit_wachtrij(pf.naam, wachtend)
                continue

            bar = self.markt.bar(order.symbool, datum)
            if bar is None:
                # Beurs dicht voor dit symbool; morgen opnieuw proberen.
                overblijvend.append(wachtend)
                continue

            if order.actie == "KOOP":
                bedrag = pf.kas_eur if deelnemer.strategie.volledig_kapitaal else None
                if bedrag is not None:
                    bedrag = bedrag / (1.0 + pf.kosten_pct / 100.0)
                pf.koop(
                    symbool=order.symbool,
                    datum=datum,
                    besluit_datum=wachtend.besluit_datum,
                    open_koers=bar.open,
                    valuta=bar.valuta,
                    eurusd=eurusd,
                    reden=order.reden,
                    zekerheid=order.zekerheid,
                    bedrag_eur=bedrag,
                )
            else:
                pf.verkoop(
                    symbool=order.symbool,
                    datum=datum,
                    besluit_datum=wachtend.besluit_datum,
                    open_koers=bar.open,
                    valuta=bar.valuta,
                    eurusd=eurusd,
                    reden=order.reden,
                    zekerheid=order.zekerheid,
                )
            # Uitgevoerd of niet mogelijk (bijvoorbeeld te weinig kas): in
            # beide gevallen is deze order afgehandeld.
            self._haal_uit_wachtrij(pf.naam, wachtend)

        deelnemer.wachtrij = overblijvend
        pf.bewaar(self.con)

    @staticmethod
    def _order_verlopen(wachtend: WachtendeOrder, datum: str) -> bool:
        grens = date.fromisoformat(wachtend.besluit_datum) + timedelta(
            days=ORDER_VERVALT_NA_DAGEN
        )
        return date.fromisoformat(datum) > grens

    def _beslis(self, deelnemer: Deelnemer, datum: str) -> None:
        """Laat de strategie beslissen op basis van de data tot en met vandaag."""
        orders = deelnemer.strategie.besluit(
            datum=datum,
            markt=self.markt,
            symbolen=deelnemer.symbolen,
            posities=dict(deelnemer.portefeuille.posities),
        )
        al_in_wachtrij = {(w.order.symbool, w.order.actie) for w in deelnemer.wachtrij}
        for order in orders:
            if (order.symbool, order.actie) in al_in_wachtrij:
                continue
            wachtend = WachtendeOrder(order=order, besluit_datum=datum)
            self._zet_in_wachtrij(deelnemer.portefeuille.naam, wachtend)
            deelnemer.wachtrij.append(wachtend)
            al_in_wachtrij.add((order.symbool, order.actie))

    # ---------- een reeks dagen draaien ----------

    def draai_periode(self, van: str, tot: str, stil: bool = True) -> int:
        """Draai alle handelsdagen tussen 'van' en 'tot' (beide inbegrepen)."""
        dagen = [d for d in self.markt.datums if van <= d <= tot]
        for i, datum in enumerate(dagen, 1):
            self.draai_dag(datum, stil=stil)
            if not stil and (i % 50 == 0 or i == len(dagen)):
                print(f"  {i}/{len(dagen)} dagen ({datum})")
        return len(dagen)

    def openstaande_orders(self) -> list[tuple[str, WachtendeOrder]]:
        """Alles wat nog moet worden uitgevoerd, voor het overzicht."""
        uit: list[tuple[str, WachtendeOrder]] = []
        for deelnemer in self.deelnemers:
            for wachtend in deelnemer.wachtrij:
                uit.append((deelnemer.portefeuille.naam, wachtend))
        return uit
