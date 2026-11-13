"""De dagelijkse ronde. Dit script draait elke nacht vanzelf op GitHub.

Wat het doet:
1. De nieuwste koersen ophalen (alleen dagen die echt af zijn).
2. De orders van gisteren uitvoeren op de openingskoers.
3. Elke strategie laten beslissen op basis van de data tot en met vandaag.
4. De AI-analist laten nadenken over de nieuwste dag.
5. De stand vastleggen en, met --telegram, een kort bericht sturen.

Het is veilig om dit script twee keer op dezelfde dag te draaien: een dag die
al verwerkt is, wordt overgeslagen, en de AI wordt niet twee keer betaald. Is
er een paar dagen niets gedraaid, dan worden die dagen vanzelf ingehaald.

Draait de meting op GitHub, dan weigert dit script lokaal te draaien. Anders
zou je lokale kopie van de database een andere meting worden dan die op
GitHub. Wil je het toch, gebruik dan --lokaal.

Gebruik:
    python scripts/dagelijks.py
    python scripts/dagelijks.py --geen-data     # koersen niet opnieuw ophalen
    python scripts/dagelijks.py --telegram      # bericht sturen
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, overzicht
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt
from bot.opzet import SLEUTEL_START, bouw_motor

# Onder deze sleutel onthoudt de database dat de meting op GitHub draait.
SLEUTEL_GITHUB = "draait_op_github"


def _op_github() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _haal_data_op(jaren: float, waarschuwingen: list[str], db: str | None = None) -> bool:
    """Roep het ophaalscript aan, voor dezelfde database. Geeft False als dat misgaat."""
    script = Path(__file__).resolve().parent / "vernieuw_data.py"
    opdracht = [sys.executable, str(script), "--jaren", str(jaren)]
    if db:
        opdracht += ["--db", str(db)]
    uitkomst = subprocess.run(
        opdracht,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if uitkomst.returncode != 0:
        print("Het ophalen van koersen is misgegaan:")
        print(uitkomst.stdout[-2000:])
        print(uitkomst.stderr[-2000:])
        return False
    for regel in uitkomst.stdout.splitlines():
        if "LET OP" in regel:
            waarschuwingen.append(regel.strip())
        if "LET OP" in regel or "opgeslagen" in regel or " via " in regel:
            print(f"  {regel.strip()}")
    return True


def _ai_ronde(
    con, cfg: dict, markt: Markt, datum: str, nieuw_nieuws: bool, ingehaald: int
) -> list[str]:
    """Laat de AI-analist nadenken over de nieuwste dag.

    Geeft een paar korte regels terug voor het dagbericht. Gaat er hier iets mis
    (geen sleutel, budget op, API onbereikbaar), dan stopt alleen de
    AI-analist. De rest van de bot heeft zijn werk dan al gedaan.
    """
    from bot.ai.budget import Budget
    from bot.ai.kostenprognose import als_tekst, maak_prognose
    from bot.ai.ronde import ai_portefeuilles, denk_na, maak_client
    from bot.data import nieuws as mod_nieuws
    from bot.opzet import ai_symbolen

    portefeuilles = ai_portefeuilles(cfg)
    if not portefeuilles:
        return []

    print("\nAI-analist")
    print("-" * 60)
    bericht: list[str] = []

    if ingehaald > 1:
        tekst = (
            f"Er zijn {ingehaald} dagen ingehaald. De AI-analist beslist alleen over de "
            f"nieuwste ({datum}): het nieuws van eerdere dagen is niet meer op te halen."
        )
        print(tekst)
        bericht.append(tekst)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("Overgeslagen: het pakket 'anthropic' is niet geïnstalleerd.")
        return ["AI-analist overgeslagen: pakket anthropic ontbreekt."]
    if not conf.sleutel("ANTHROPIC_API_KEY"):
        print("Overgeslagen: geen ANTHROPIC_API_KEY gevonden (zie .env.example).")
        return ["AI-analist overgeslagen: geen API-key."]

    budget = Budget(con, cfg)
    if budget.is_op():
        print(budget.samenvatting())
        print("Het budget van deze maand is op. De AI-analist slaat vandaag over.")
        return [f"AI-analist overgeslagen: budget van ${budget.max_usd:.2f} is op."]

    symbolen = ai_symbolen(cfg, markt)
    if nieuw_nieuws:
        aantal = mod_nieuws.vernieuw(con, symbolen)
        print(f"{aantal} nieuwskoppen opgehaald voor {len(symbolen)} symbolen.")

    client = maak_client(con, cfg)
    kosten_vandaag = 0.0
    for naam, model in portefeuilles:
        print(f"\n  {naam}" + (f" (alles op {model})" if model else ""))
        uitkomst = denk_na(con, cfg, client, markt, naam, datum, symbolen, model_override=model)
        kosten_vandaag += uitkomst.kosten_usd
        if uitkomst.al_gedaan:
            print(f"    {uitkomst.al_gedaan} symbolen waren vandaag al beoordeeld; niet opnieuw betaald.")
        print(f"    {len(uitkomst.orders)} orders klaargezet, kosten ${uitkomst.kosten_usd:.4f}")
        if uitkomst.fouten:
            bericht.append(f"{naam}: {len(uitkomst.fouten)} fout(en), bijv. {uitkomst.fouten[0][:120]}")
        if uitkomst.gestopt:
            print(f"    GESTOPT: {uitkomst.gestopt}")
            bericht.append(f"{naam} gestopt: {uitkomst.gestopt[:200]}")
            break

    prognose = maak_prognose(con, cfg)
    print()
    print(budget.samenvatting())
    print()
    print(als_tekst(prognose))

    regel = f"AI-kosten vandaag ${kosten_vandaag:.3f}, deze maand ${budget.uitgegeven():.2f} van ${budget.max_usd:.2f}."
    if prognose.maand_usd is not None:
        oordeel = "past" if prognose.past else "PAST NIET"
        regel += f" Verwachting: ${prognose.maand_usd:.2f}/maand ({oordeel}, {prognose.rondes} ronde(s) gemeten)."
    bericht.append(regel)
    return bericht


def ronde(args) -> tuple[int, str | None]:
    """De ronde zelf. Geeft de afsluitcode en het dagbericht voor Telegram."""
    cfg = conf.laad()
    con = database.verbind(args.db)
    waarschuwingen: list[str] = []

    print(f"Dagelijkse ronde, {date.today().isoformat()}")
    print("=" * 60)

    # ---- 0. lokaal of op GitHub? ----
    if _op_github():
        database.zet_instelling(con, SLEUTEL_GITHUB, "ja")
    elif database.lees_instelling(con, SLEUTEL_GITHUB) == "ja" and not args.lokaal:
        print(
            "\nDeze meting draait op GitHub. Een lokale ronde zou je kopie van de\n"
            "database een andere meting maken dan die op GitHub, en dan botst het\n"
            "bij de volgende 'git pull'.\n\n"
            "Wil je de laatste stand zien? Haal hem op met 'git pull' en gebruik\n"
            "scripts/stand.py. Weet je zeker dat je lokaal wilt draaien, gebruik\n"
            "dan --lokaal."
        )
        con.close()
        return 2, None

    # ---- 1. koersen ----
    if args.geen_data:
        print("\nKoersen ophalen overgeslagen.")
    else:
        print("\nKoersen ophalen...")
        if not _haal_data_op(args.jaren, waarschuwingen, db=args.db):
            con.close()
            return 1, None

    markt = Markt.uit_database(con)
    if not markt.datums:
        print("Geen koersen in de database. Draai eerst: python scripts/vernieuw_data.py")
        con.close()
        return 1, None
    wk = Wisselkoers.uit_database(con)
    if len(wk) == 0:
        print("Geen wisselkoersen in de database.")
        con.close()
        return 1, None

    laatste_koersdag = markt.datums[-1]
    tot = min(args.tot or laatste_koersdag, laatste_koersdag)

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
        con, cfg, markt, wk, begin=start, stil=False,
        met_ai=bool(cfg.get("ai", {}).get("actief")),
    )
    print(f"{len(motor.deelnemers)} portefeuilles.")

    # Begin bij de oudste dag die nog niet voor iedereen af is.
    laatst_gedaan = [
        database.laatste_verwerkte_dag(con, d.portefeuille.naam) for d in motor.deelnemers
    ]
    vanaf = min(laatst_gedaan) if laatst_gedaan and all(laatst_gedaan) else start
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
    ai_regels: list[str] = []
    if not args.geen_ai:
        ai_regels = _ai_ronde(
            con, cfg, markt, tot, nieuw_nieuws=not args.geen_data, ingehaald=len(te_doen)
        )

    # ---- 6. het dagbericht ----
    print("\n" + "=" * 60)
    print(overzicht.dagbericht(con, tot))
    print("=" * 60)

    extra = list(ai_regels)
    if waarschuwingen:
        extra.append("Let op bij het ophalen van koersen:")
        extra.extend(f"  {w}" for w in waarschuwingen[:5])
    telegram_tekst = overzicht.telegram_dagbericht(con, tot, extra=extra)

    con.close()
    return 0, telegram_tekst


def main() -> int:
    p = argparse.ArgumentParser(description="De dagelijkse ronde van de bot")
    p.add_argument("--geen-data", action="store_true", help="koersen niet ophalen")
    p.add_argument("--jaren", type=float, default=2.0, help="hoeveel geschiedenis ophalen")
    p.add_argument("--tot", default=None, help="tot en met deze datum (JJJJ-MM-DD)")
    p.add_argument("--db", default=None, help="pad naar de database")
    p.add_argument("--start", default=None, help="startdatum van de meting vastleggen")
    p.add_argument("--geen-ai", action="store_true", help="de AI-analist overslaan")
    p.add_argument("--telegram", action="store_true", help="een bericht sturen")
    p.add_argument("--lokaal", action="store_true", help="ook lokaal draaien als de meting op GitHub loopt")
    args = p.parse_args()

    from bot import telegram

    try:
        code, tekst = ronde(args)
    except Exception as fout:
        traceback.print_exc()
        if args.telegram and telegram.is_ingesteld():
            try:
                telegram.stuur(
                    "Paper trading: de dagelijkse ronde is mislukt.\n\n"
                    f"{type(fout).__name__}: {str(fout)[:500]}\n\n"
                    "De database is niet half bijgewerkt: elke dag gaat in een keer of "
                    "niet. Kijk in de logboeken van GitHub Actions voor details."
                )
            except telegram.TelegramFout as tfout:
                print(f"Ook de foutmelding naar Telegram lukte niet: {tfout}")
        return 1

    if args.telegram:
        if not telegram.is_ingesteld():
            print("\nTelegram overgeslagen: TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt.")
        elif code != 0 or tekst is None:
            try:
                telegram.stuur(f"Paper trading: de dagelijkse ronde stopte (code {code}). Zie de logboeken.")
            except telegram.TelegramFout as tfout:
                print(f"Telegram lukte niet: {tfout}")
        else:
            try:
                delen = telegram.stuur(tekst, stil=True)
                print(f"\nTelegram: bericht verstuurd ({delen} deel/delen).")
            except telegram.TelegramFout as tfout:
                print(f"\nTelegram lukte niet: {tfout}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
