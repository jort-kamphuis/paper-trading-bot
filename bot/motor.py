"""De motor: laat de strategieën dag na dag hun werk doen.

De regel die alles eerlijk houdt: een strategie beslist op basis van de data
tot en met dag T, en de trade wordt uitgevoerd op de openingskoers van de
eerstvolgende handelsdag. Zo kan geen enkele strategie een koers gebruiken die
op het moment van beslissen nog niet bekend was.

Dezelfde motor draait de backtest en de echte dagelijkse ronde. Dat is met
opzet: zo kan de backtest niet per ongeluk andere regels hanteren.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

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

    # ---------- opzetten ----------

    def voeg_toe(
        self, strategie: Strategie, symbolen: list[str], gestart_op: str | None = None
    ) -> Deelnemer:
        """Zet een strategie klaar met zijn portefeuille (nieuw of uit de database)."""
        instellingen = self.cfg["algemeen"]
        naam = strategie.naam
        pf = Portefeuille.uit_database(self.con, naam, instellingen)
        if pf is None:
            pf = nieuwe_portefeuille(naam, strategie.naam, instellingen, gestart_op)
        deelnemer = Deelnemer(strategie=strategie, portefeuille=pf, symbolen=list(symbolen))
        self.deelnemers.append(deelnemer)
        return deelnemer

    # ---------- een dag draaien ----------

    def draai_dag(self, datum: str, stil: bool = True) -> None:
        """Doe alles voor een dag: eerst uitvoeren, dan waarderen, dan beslissen."""
        koersen_eur = self.markt.slotkoersen_eur(datum, self.wisselkoers)

        for deelnemer in self.deelnemers:
            self._voer_wachtrij_uit(deelnemer, datum, stil=stil)
            deelnemer.portefeuille.bewaar_waarde(self.con, datum, koersen_eur)
            self._beslis(deelnemer, datum)

        self.con.commit()

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
            deelnemer.wachtrij.append(WachtendeOrder(order=order, besluit_datum=datum))

    # ---------- een reeks dagen draaien ----------

    def draai_periode(self, van: str, tot: str, stil: bool = True) -> int:
        """Draai alle handelsdagen tussen 'van' en 'tot' (beide inbegrepen)."""
        dagen = [d for d in self.markt.datums if van <= d <= tot]
        for i, datum in enumerate(dagen, 1):
            self.draai_dag(datum, stil=stil)
            if not stil and (i % 50 == 0 or i == len(dagen)):
                print(f"  {i}/{len(dagen)} dagen ({datum})")
        return len(dagen)
