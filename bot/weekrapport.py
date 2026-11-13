"""Het weekrapport in zijn geheel.

Volgorde:
1. Lopende challengers beoordelen die lang genoeg hebben gelopen.
2. De cijfers per strategie (bot/rapport.py).
3. Claude schrijft drie conclusies en hooguit een voorstel, als er een sleutel
   en budget is. Zonder sleutel komt het rapport er gewoon, zonder conclusies.
4. Een voorstel dat de toets doorstaat, start als challenger.
5. Alles wordt opgeslagen in de tabel 'rapporten'. Een rapport over een dag
   die al een rapport heeft, wordt niet opnieuw gemaakt: geen dubbele kosten.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from bot import challenger, rapport
from bot.ai.budget import Budget, BudgetOp
from bot.challenger import Voorstel
from bot.tekst import dollar, getal
from bot.opzet import SLEUTEL_START


@dataclass
class Weekrapport:
    datum: str
    tekst: str
    extra_berichten: list[str] = field(default_factory=list)
    al_gemaakt: bool = False


def is_rapportdag(cfg: dict, nu: datetime | None = None) -> bool:
    nu = nu or datetime.now(timezone.utc)
    return nu.weekday() == int(cfg["rapport"]["weekdag"])


def is_nodig(con: sqlite3.Connection, cfg: dict, tot: str, nu: datetime | None = None) -> bool:
    """Moet er vannacht een weekrapport komen?

    Op de rapportdag, of later als dat de vorige keer niet lukte (bijvoorbeeld
    omdat de koersen die nacht niet compleet waren). Zo valt er geen week weg.
    """
    laatste = con.execute("SELECT MAX(datum) d FROM rapporten").fetchone()["d"]
    if laatste and (date.fromisoformat(tot) - date.fromisoformat(laatste)).days < 6:
        return False  # deze week is er al een
    if is_rapportdag(cfg, nu):
        return True
    return bool(laatste) and (date.fromisoformat(tot) - date.fromisoformat(laatste)).days >= 7


def _proef_tekst(con: sqlite3.Connection, cfg: dict, tot: str, kal: list[str]) -> str:
    """Wat er over lopende en afgeronde proeven te zeggen is."""
    regels = ["Proeven (challengers):"]
    weken = int(cfg["challenger"]["weken"])
    lopend = challenger.actieve(con)
    if not lopend:
        regels.append("  Er loopt op dit moment geen proef.")
    for c in lopend:
        cijfers = challenger.vergelijk_paar(con, c, tot, kal)
        dagen = (date.fromisoformat(tot) - date.fromisoformat(c["gestart_op"])).days
        stand = ""
        if cijfers:
            ra, rb, _ = cijfers
            stand = f" Tot nu toe: huidig {getal(ra, 1, teken=True)}%, voorstel {getal(rb, 1, teken=True)}%."
        if c["status"] == "loopt":
            regels.append(
                f"  Challenger {c['id']}: {c['strategie']}.{c['parameter']} "
                f"{challenger._mooi(c['oude_waarde'])} naar {challenger._mooi(c['nieuwe_waarde'])}, "
                f"dag {dagen} van {weken * 7}.{stand}"
            )
        else:
            regels.append(f"  Challenger {c['id']} wacht op je akkoord. {c['uitslag']}")
    afgerond = con.execute(
        """SELECT * FROM challengers WHERE status IN ('aangenomen', 'afgewezen')
           ORDER BY id DESC LIMIT 3"""
    ).fetchall()
    for c in afgerond:
        regels.append(f"  Eerder: {c['uitslag']}")
    wijzigingen = con.execute("SELECT * FROM wijzigingen ORDER BY id").fetchall()
    if wijzigingen:
        regels.append("Aangepaste instellingen sinds de start:")
        for w in wijzigingen:
            regels.append(
                f"  {w['datum']}: {w['strategie']}.{w['parameter']} "
                f"{challenger._mooi(w['oude_waarde'])} -> {challenger._mooi(w['nieuwe_waarde'])} "
                f"(akkoord: {w['akkoord']})"
            )
    return "\n".join(regels)


def maak(
    con: sqlite3.Connection,
    cfg: dict,
    tot: str,
    client=None,
    stil: bool = False,
) -> Weekrapport:
    bestaand = con.execute("SELECT tekst FROM rapporten WHERE datum = ?", (tot,)).fetchone()
    if bestaand:
        return Weekrapport(datum=tot, tekst=bestaand["tekst"], al_gemaakt=True)

    kal = rapport.kalender(con, cfg)
    extra: list[str] = []

    # ---- 1. challengers beoordelen ----
    for uitslag in challenger.beoordeel(con, cfg, tot, kal):
        if uitslag.beter:
            c = con.execute("SELECT * FROM challengers WHERE id = ?", (uitslag.challenger_id,)).fetchone()
            extra.append(challenger.vraag_om_akkoord(c))
    # Wie nog op een akkoord wacht, krijgt elke week opnieuw de vraag.
    for c in challenger.actieve(con):
        if c["status"] == "wacht_op_akkoord":
            vraag = challenger.vraag_om_akkoord(c)
            if vraag not in extra:
                extra.append(vraag)

    # ---- 2. cijfers ----
    start = con.execute(
        "SELECT waarde FROM instellingen WHERE sleutel = ?", (SLEUTEL_START,)
    ).fetchone()
    cijfers = rapport.alle_kerncijfers(con, cfg, tot)
    cijfers_tekst = rapport.cijfers_als_tekst(cijfers, tot, start["waarde"] if start else None)
    proef_tekst = _proef_tekst(con, cfg, tot, kal)

    # ---- 3. Claude ----
    conclusies: list[str] = []
    voorstel_json = None
    ai_regels: list[str] = []
    if client is None:
        ai_regels.append(
            "Geen conclusies van Claude deze week (geen API-key, AI uitgezet, of niet de echte database)."
        )
    else:
        from bot.ai.weekoordeel import vraag_oordeel

        try:
            oordeel = vraag_oordeel(client, cfg, cijfers_tekst, proef_tekst)
        except BudgetOp as fout:
            oordeel = None
            ai_regels.append(f"Geen conclusies van Claude: {fout}")
        except Exception as fout:
            oordeel = None
            ai_regels.append(f"Geen conclusies van Claude: {type(fout).__name__}: {str(fout)[:150]}")

        if oordeel is not None and oordeel.probleem:
            ai_regels.append(f"Geen conclusies van Claude: {oordeel.probleem}.")
        elif oordeel is not None:
            conclusies = oordeel.conclusies
            ai_regels.append("Conclusies (geschreven door Claude op basis van de cijfers hierboven):")
            for i, c in enumerate(conclusies, 1):
                ai_regels.append(f"{i}. {c}")
            if len(conclusies) < 3:
                ai_regels.append(f"(Claude gaf er {len(conclusies)} in plaats van 3.)")

            v = oordeel.voorstel
            if v is None:
                ai_regels.append("\nVoorstel: geen. Er verandert niets.")
            else:
                reden = challenger.toets_voorstel(cfg, v, loopt_al=bool(challenger.actieve(con)))
                toets_tekst = None
                if reden is None:
                    # Eerst de backtest: kiezen op de eerdere jaren, controleren op
                    # het laatste jaar. Zakt het voorstel, dan start het niet.
                    from bot.challenger_toets import toets as backtest_toets

                    geslaagd, toets_tekst = backtest_toets(con, cfg, v)
                    if not geslaagd:
                        reden = f"de backtest is niet gehaald ({toets_tekst})"
                voorstel_json = {**v.__dict__, "afgewezen_door_bot": reden, "backtest": toets_tekst}
                if reden:
                    ai_regels.append(
                        f"\nVoorstel van Claude: {v.strategie}.{v.parameter} naar "
                        f"{challenger._mooi(v.nieuwe_waarde)}. Niet gestart: {reden}."
                    )
                else:
                    nummer = challenger.start(con, cfg, v, voorgesteld_op=tot, gestart_op=tot, toets=toets_tekst)
                    huidig = cfg["strategieen"][v.strategie][v.parameter]
                    weken = int(cfg["challenger"]["weken"])
                    ai_regels.append(
                        f"\nVoorstel: {v.strategie}.{v.parameter} van {challenger._mooi(huidig)} "
                        f"naar {challenger._mooi(v.nieuwe_waarde)}.\n{v.onderbouwing}\n"
                        f"Backtest: {toets_tekst}\n"
                        f"Dit start als challenger {nummer}: vanaf de volgende ronde draaien twee "
                        f"nieuwe portefeuilles naast elkaar, een met de huidige en een met de "
                        f"voorgestelde instelling. Na {weken} weken krijg je de uitslag. Een lopende "
                        f"portefeuille verandert nooit; na jouw akkoord gaat de nieuwe versie als "
                        f"eigen portefeuille verder."
                    )

    # ---- 3b. alleen in de proefweek: een testvoorstel ----
    # Zonder lopende of eerdere proef valt de akkoordroute niet te testen. Doet
    # Claude geen voorstel, dan start de bot een testvoorstel, duidelijk als
    # zodanig gemarkeerd, en alleen in de proefdatabase.
    test = (cfg.get("proefweek") or {}).get("testvoorstel") if cfg.get("modus") == "proefweek" else None
    eerder = con.execute("SELECT COUNT(*) n FROM challengers").fetchone()["n"]
    if test and not eerder:
        v = Voorstel(
            strategie=test["strategie"],
            parameter=test["parameter"],
            nieuwe_waarde=float(test["nieuwe_waarde"]),
            onderbouwing="TESTVOORSTEL van de proefweek (niet van Claude), om de akkoordroute te testen.",
        )
        reden = challenger.toets_voorstel(cfg, v, loopt_al=False)
        if reden is None:
            nummer = challenger.start(con, cfg, v, voorgesteld_op=tot, gestart_op=tot)
            ai_regels.append(
                f"\nProefweek: Claude deed geen voorstel, dus de bot startte TESTVOORSTEL "
                f"{nummer} ({v.strategie}.{v.parameter} naar {challenger._mooi(v.nieuwe_waarde)}). "
                f"Dat is alleen om de akkoordroute te testen."
            )

    # ---- 4. kosten ----
    budget = Budget(con, cfg)
    kosten = f"AI-kosten deze maand: {dollar(budget.uitgegeven(), 2)} van {dollar(budget.max_usd, 2)}."

    # ---- 5. klopten de kansen van de AI? (apart per soort vraag) ----
    from bot.ai import kalibratie

    heeft_kansen = con.execute("SELECT 1 FROM ai_besluiten WHERE kans IS NOT NULL LIMIT 1").fetchone()
    kalibratie_tekst = kalibratie.als_tekst(con) if heeft_kansen else ""

    tekst = "\n\n".join(
        d for d in [cijfers_tekst, proef_tekst, kalibratie_tekst, "\n".join(ai_regels), kosten,
                    "Nep-geld, geen beleggingsadvies."] if d
    )

    con.execute(
        "INSERT INTO rapporten (datum, tekst, conclusies, voorstel, gemaakt_op) VALUES (?,?,?,?,?)",
        (
            tot,
            tekst,
            json.dumps(conclusies, ensure_ascii=False),
            json.dumps(voorstel_json, ensure_ascii=False) if voorstel_json else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()
    return Weekrapport(datum=tot, tekst=tekst, extra_berichten=extra)
