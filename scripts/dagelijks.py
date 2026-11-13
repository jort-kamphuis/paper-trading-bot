"""De dagelijkse ronde. Dit script draait straks elke nacht vanzelf.

Wat het doet:
1. De nieuwste koersen ophalen.
2. Elke strategie laten beslissen op basis van de data tot en met vandaag.
3. De orders van gisteren uitvoeren op de openingskoers van vandaag.
4. De stand van elke portefeuille vastleggen.

Het is veilig om dit script twee keer op dezelfde dag te draaien: een dag die
al verwerkt is, wordt overgeslagen. Is er een paar dagen niets gedraaid
(storing, computer uit), dan worden die dagen vanzelf ingehaald.

Gebruik:
    python scripts/dagelijks.py
    python scripts/dagelijks.py --geen-data     # koersen niet opnieuw ophalen
    python scripts/dagelijks.py --tot 2026-11-01
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, overzicht
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt
from bot.opzet import SLEUTEL_START, bouw_motor


def _haal_data_op(jaren: float) -> bool:
    """Roep het ophaalscript aan. Geeft False als dat misgaat."""
    script = Path(__file__).resolve().parent / "vernieuw_data.py"
    uitkomst = subprocess.run(
        [sys.executable, str(script), "--jaren", str(jaren)],
        capture_output=True,
        text=True,
    )
    if uitkomst.returncode != 0:
        print("Het ophalen van koersen is misgegaan:")
        print(uitkomst.stdout[-2000:])
        print(uitkomst.stderr[-2000:])
        return False
    for regel in uitkomst.stdout.splitlines():
        if "LET OP" in regel or "dagkoersen opgeslagen" in regel:
            print(f"  {regel.strip()}")
    return True


def _ai_ronde(con, cfg: dict, markt: Markt, datum: str, nieuw_nieuws: bool, ingehaald: int) -> None:
    """Laat de AI-analist nadenken over de nieuwste dag.

    Gaat er hier iets mis (geen sleutel, budget op, API onbereikbaar), dan
    stopt alleen de AI-analist. De rest van de bot heeft zijn werk al gedaan.
    """
    from bot.ai.budget import Budget
    from bot.ai.ronde import ai_portefeuilles, denk_na, maak_client
    from bot.data import nieuws as mod_nieuws
    from bot.opzet import ai_symbolen

    portefeuilles = ai_portefeuilles(cfg)
    if not portefeuilles:
        return

    print("\nAI-analist")
    print("-" * 60)

    if ingehaald > 1:
        print(
            f"Er zijn {ingehaald} dagen ingehaald. De AI-analist beslist alleen over de\n"
            f"nieuwste ({datum}): het nieuws van eerdere dagen is niet meer op te halen,\n"
            f"en met nieuws van later beslissen zou vooruitkijken zijn."
        )

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("Overgeslagen: het pakket 'anthropic' is niet geïnstalleerd.")
        return
    if not conf.sleutel("ANTHROPIC_API_KEY"):
        print("Overgeslagen: geen ANTHROPIC_API_KEY gevonden (zie .env.example).")
        return

    budget = Budget(con, cfg)
    if budget.is_op():
        print(budget.samenvatting())
        print("Het budget van deze maand is op. De AI-analist slaat vandaag over.")
        return

    symbolen = ai_symbolen(cfg, markt)
    if nieuw_nieuws:
        aantal = mod_nieuws.vernieuw(con, symbolen)
        print(f"{aantal} nieuwskoppen opgehaald voor {len(symbolen)} symbolen.")

    client = maak_client(con, cfg)
    for naam, model in portefeuilles:
        print(f"\n  {naam}" + (f" (alles op {model})" if model else ""))
        uitkomst = denk_na(con, cfg, client, markt, naam, datum, symbolen, model_override=model)
        if uitkomst.al_gedaan:
            print(f"    {uitkomst.al_gedaan} symbolen waren vandaag al beoordeeld; niet opnieuw betaald.")
        print(
            f"    {len(uitkomst.orders)} orders klaargezet, kosten ${uitkomst.kosten_usd:.4f}"
        )
        if uitkomst.gestopt:
            print(f"    GESTOPT: {uitkomst.gestopt}")
            break

    from bot.ai.kostenprognose import als_tekst, maak_prognose

    print()
    print(budget.samenvatting())
    print()
    print(als_tekst(maak_prognose(con, cfg)))


def main() -> int:
    p = argparse.ArgumentParser(description="De dagelijkse ronde van de bot")
    p.add_argument("--geen-data", action="store_true", help="koersen niet ophalen")
    p.add_argument("--jaren", type=float, default=2.0, help="hoeveel geschiedenis ophalen")
    p.add_argument("--tot", default=None, help="tot en met deze datum (JJJJ-MM-DD)")
    p.add_argument("--db", default=None, help="pad naar de database")
    p.add_argument("--start", default=None, help="startdatum van de meting vastleggen")
    p.add_argument("--geen-ai", action="store_true", help="de AI-analist overslaan")
    args = p.parse_args()

    cfg = conf.laad()
    con = database.verbind(args.db)

    print(f"Dagelijkse ronde, {date.today().isoformat()}")
    print("=" * 60)

    # ---- 1. koersen ----
    if args.geen_data:
        print("\nKoersen ophalen overgeslagen.")
    else:
        print("\nKoersen ophalen...")
        if not _haal_data_op(args.jaren):
            return 1

    markt = Markt.uit_database(con)
    if not markt.datums:
        print("Geen koersen in de database. Draai eerst: python scripts/vernieuw_data.py")
        return 1
    wk = Wisselkoers.uit_database(con)
    if len(wk) == 0:
        print("Geen wisselkoersen in de database.")
        return 1

    laatste_koersdag = markt.datums[-1]
    tot = args.tot or laatste_koersdag
    if tot > laatste_koersdag:
        tot = laatste_koersdag

    # ---- 2. startdatum van de meting ----
    start = database.lees_instelling(con, SLEUTEL_START)
    if args.start:
        start = args.start
        database.zet_instelling(con, SLEUTEL_START, start)
        print(f"\nStartdatum van de meting gezet op {start}.")
    elif start is None:
        # Begin op een dag dat de aandelenbeurs open was. Anders zou de meting
        # beginnen op een dag waarop alleen crypto handelde, en starten de
        # aandelenstrategieën en de meetlat SPY een dag later dan de rest.
        kalender_symbool = cfg["rapport"]["kalender_symbool"]
        beursdagen = [d for d in markt.handelsdagen_van(kalender_symbool) if d <= tot]
        start = beursdagen[-1] if beursdagen else tot
        database.zet_instelling(con, SLEUTEL_START, start)
        print(f"\nEerste ronde. De meting begint op {start},")
        print(f"de laatste dag dat {kalender_symbool} handelde, zodat alle")
        print("portefeuilles op hetzelfde moment van start gaan.")
        print("Elke portefeuille begint met 1.000 euro nep-geld.")
    else:
        print(f"\nDe meting loopt sinds {start}.")

    # ---- 3. de motor ----
    motor = bouw_motor(
        con, cfg, markt, wk, begin=start, stil=False, met_ai=bool(cfg.get("ai", {}).get("actief"))
    )
    print(f"{len(motor.deelnemers)} portefeuilles.")

    # Begin bij de oudste dag die nog niet voor iedereen af is.
    laatst_gedaan = [
        database.laatste_verwerkte_dag(con, d.portefeuille.naam) for d in motor.deelnemers
    ]
    if all(laatst_gedaan) and laatst_gedaan:
        vanaf = min(d for d in laatst_gedaan if d)
    else:
        vanaf = start
    vanaf = max(vanaf, start)

    te_doen = [d for d in markt.datums if vanaf <= d <= tot]
    print(f"Te verwerken: {vanaf} tot en met {tot} ({len(te_doen)} dagen in de data).")

    # ---- 4. draaien ----
    if te_doen:
        motor.draai_periode(vanaf, tot, stil=True)
        if motor.overgeslagen_dagen:
            print(f"{motor.overgeslagen_dagen} dagstanden waren al verwerkt en overgeslagen.")
    else:
        print("Geen nieuwe koersdag voor de motor.")

    # ---- 5. de AI-analist ----
    if not args.geen_ai:
        _ai_ronde(con, cfg, markt, tot, nieuw_nieuws=not args.geen_data, ingehaald=len(te_doen))

    # ---- 6. het dagbericht ----
    print("\n" + "=" * 60)
    print(overzicht.dagbericht(con, tot))
    print("=" * 60)

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
