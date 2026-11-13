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
from bot.tekst import dollar

# Onder deze sleutel onthoudt de database dat de meting op GitHub draait.
SLEUTEL_GITHUB = "draait_op_github"


def _op_github() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _ai_mag(args) -> bool:
    """De AI kost echt geld. Dat mag alleen op de echte database.

    Draait iemand de ronde op een kopie (om iets uit te proberen), dan slaat de
    bot de AI over. Anders betaal je voor besluiten in een bestand dat je
    daarna weggooit, en weet de budgetteller van de echte meting er niets van.
    """
    if args.db is None or args.ai_op_kopie:
        return True
    # De echte database, of tijdens de proefweek de proefweekdatabase.
    from bot import modus

    toegestaan = {conf.DB_PAD.resolve()}
    overlay = modus.lees_proefweek()
    if overlay and "proefweek" in overlay:
        toegestaan.add((conf.PROJECT / overlay["proefweek"]["database"]).resolve())
    return Path(args.db).resolve() in toegestaan


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
        if "LET OP" in regel or "opgeslagen" in regel or " via " in regel or "herkansing" in regel:
            print(f"  {regel.strip()}")
    # Wat yfinance en ccxt zelf melden, komt op een apart kanaal. Bij problemen
    # tonen we dat, anders weet je niet waarom iets ontbrak.
    if waarschuwingen and uitkomst.stderr.strip():
        print("  Meldingen van de koersbronnen:")
        for regel in uitkomst.stderr.strip().splitlines()[-15:]:
            print(f"    {regel}")
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
        return [f"AI-analist overgeslagen: budget van {dollar(budget.max_usd, 2)} is op."]

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
        print(f"    {len(uitkomst.orders)} orders klaargezet, kosten {dollar(uitkomst.kosten_usd, 4)}")
        if uitkomst.fouten:
            bericht.append(f"{naam}: {len(uitkomst.fouten)} fout(en), bijv. {uitkomst.fouten[0][:120]}")
        if uitkomst.gestopt:
            print(f"    GESTOPT: {uitkomst.gestopt}")
            bericht.append(f"{naam} gestopt: {uitkomst.gestopt[:200]}")
            break

    print()
    print(budget.samenvatting())
    if cfg.get("modus") == "proefweek":
        # In de proefweek is de vraag niet of 48 symbolen op Opus in een maand
        # passen, maar wat de echte meting straks kost. Dat meet proef_ai_mix.
        bericht.append(
            f"AI-kosten vandaag {dollar(kosten_vandaag, 2)}, proefweek tot nu toe "
            f"{dollar(budget.uitgegeven(), 2)} van {dollar(budget.max_usd, 2)}."
        )
        schatting = _schatting_echte_meting(con)
        if schatting:
            print(schatting)
            bericht.append(schatting)
        return bericht

    prognose = maak_prognose(con, cfg)
    print()
    print(als_tekst(prognose))
    regel = f"AI-kosten vandaag {dollar(kosten_vandaag, 2)}, deze maand {dollar(budget.uitgegeven(), 2)} van {dollar(budget.max_usd, 2)}."
    if prognose.maand_usd is not None:
        oordeel = "past" if prognose.past else "PAST NIET"
        regel += f" Verwachting: {dollar(prognose.maand_usd, 2)}/maand ({oordeel}, {prognose.rondes} ronde(s) gemeten)."
    bericht.append(regel)
    return bericht


def _schatting_echte_meting(con) -> str | None:
    """Wat de echte meting per maand kost, gemeten aan proef_ai_mix en proef_ai_haiku.

    Gerekend met de gewone instellingen (15 symbolen), want zo draait de echte
    meting straks.
    """
    from bot.ai.kostenprognose import maak_prognose

    normaal = conf.laad()
    p = maak_prognose(con, normaal)
    mix = p.per_portefeuille.get("proef_ai_mix")
    haiku = p.per_portefeuille.get("proef_ai_haiku")
    if mix is None:
        return None
    rapport = 0.0
    for regel in p.uitleg:
        if regel.startswith("weekrapport:"):
            try:
                rapport = float(regel.rsplit("$", 1)[1].split()[0])
            except (IndexError, ValueError):
                rapport = 0.0
    totaal = mix + (haiku or 0.0) + rapport
    budget = float(normaal["ai"]["budget"]["max_usd_per_maand"])
    oordeel = "past" if totaal <= budget else "PAST NIET"
    return (
        f"Echte meting (mix + Haiku-controle, 15 symbolen) naar schatting {dollar(totaal, 2)} per maand "
        f"tegen een budget van {dollar(budget, 2)}: {oordeel}. Gemeten over {p.rondes} ronde(s)."
    )


SLEUTEL_TELEGRAM_OFFSET = "telegram_offset"
# Hierin zet de ronde een beschrijving van een goedgekeurde wijziging, zodat
# GitHub Actions die in het commitbericht kan zetten. Staat in .gitignore.
WIJZIGING_BESTAND = conf.PROJECT / ".wijziging.txt"


def _verwerk_antwoorden(con, datum: str, droog: bool = False) -> list[str]:
    """Lees je /akkoord en /afwijzen uit Telegram en voer ze uit.

    Alleen berichten uit jouw eigen chat tellen, en alleen die twee opdrachten.
    Al het andere wordt genegeerd. Geeft de antwoorden terug die de bot stuurt.
    """
    from bot import challenger, telegram

    if not telegram.is_ingesteld():
        return []
    token = conf.sleutel("TELEGRAM_BOT_TOKEN")
    chat = conf.sleutel("TELEGRAM_CHAT_ID")
    opgeslagen = database.lees_instelling(con, SLEUTEL_TELEGRAM_OFFSET)
    try:
        berichten, volgende = telegram.lees_berichten(
            token, chat, int(opgeslagen) if opgeslagen else None
        )
    except telegram.TelegramFout as fout:
        print(f"Telegram-berichten lezen lukte niet: {fout}")
        return []

    antwoorden: list[str] = []
    wijzigingen: list[str] = []
    for bericht in berichten:
        opdracht = challenger.lees_opdracht(bericht["tekst"])
        if opdracht is None:
            continue
        actie, nummer = opdracht
        antwoord = challenger.verwerk(
            con, conf.CONFIG_PAD, actie, nummer, wanneer=bericht["wanneer"], datum=datum,
            droog=droog,
        )
        print(f"Telegram: /{actie} {nummer} -> {antwoord}")
        antwoorden.append(antwoord)
        if actie == "akkoord" and antwoord.startswith("Akkoord verwerkt"):
            wijzigingen.append(antwoord)

    if volgende is not None:
        database.zet_instelling(con, SLEUTEL_TELEGRAM_OFFSET, str(volgende))
        try:
            telegram.bevestig(token, volgende)
        except telegram.TelegramFout:
            pass  # de offset staat opgeslagen; volgende keer slaan we ze alsnog over
    if wijzigingen:
        WIJZIGING_BESTAND.write_text("\n".join(wijzigingen) + "\n", encoding="utf-8")
    return antwoorden


def _weigert_lokaal(con, args) -> bool:
    """True als deze database op GitHub draait en we hier niet lokaal mogen schrijven."""
    if _op_github():
        database.zet_instelling(con, SLEUTEL_GITHUB, "ja")
        return False
    return database.lees_instelling(con, SLEUTEL_GITHUB) == "ja" and not args.lokaal


LOKAAL_UITLEG = (
    "\nDeze meting draait op GitHub. Een lokale ronde zou je kopie van de\n"
    "database een andere meting maken dan die op GitHub, en dan botst het\n"
    "bij de volgende 'git pull'.\n\n"
    "Wil je de laatste stand zien? Haal hem op met 'git pull' en gebruik\n"
    "scripts/stand.py. Weet je zeker dat je lokaal wilt draaien, gebruik\n"
    "dan --lokaal."
)


def ronde(args) -> tuple[int, str | None]:
    """De ronde zelf. Geeft de afsluitcode en het dagbericht voor Telegram."""
    from datetime import datetime, timezone

    from bot import modus, omschakelen

    vandaag_utc = datetime.now(timezone.utc).date()
    waarschuwingen: list[str] = []
    args.berichten = []  # extra berichten voor Telegram (weekrapport, vragen)
    voor_aan: list[str] = []  # regels bovenaan het dagbericht

    print(f"Dagelijkse ronde, {date.today().isoformat()} (UTC {vandaag_utc.isoformat()})")
    print("=" * 60)

    # ---- 0. van de proefweek naar de echte meting ----
    # Alleen in de gewone opstelling (zonder --db): vanzelf na de proefweek, of
    # als je het zelf vraagt met --omschakelen ja.
    overlay = modus.lees_proefweek()
    if args.db is None:
        con_echt = database.verbind(conf.DB_PAD)
        if args.omschakelen == "ja" or omschakelen.is_nodig(con_echt, overlay, vandaag_utc):
            if _weigert_lokaal(con_echt, args):
                print(LOKAAL_UITLEG)
                con_echt.close()
                return 2, None
            con_proef = None
            if overlay and "proefweek" in overlay:
                con_proef = database.verbind(conf.PROJECT / overlay["proefweek"]["database"])
            melding = omschakelen.voer_uit(con_echt, con_proef, overlay, vandaag_utc)
            if con_proef is not None:
                con_proef.close()
            print("\n" + melding)
            voor_aan.append(melding)
        con_echt.close()

    cfg = modus.laad()
    db = args.db or str(modus.database_pad(cfg))
    args.label = cfg.get("label", "")
    print(f"Modus: {cfg['modus']}, database: {Path(db).name}")
    con = database.verbind(db)

    # ---- 0b. lokaal of op GitHub? ----
    if _weigert_lokaal(con, args):
        print(LOKAAL_UITLEG)
        con.close()
        return 2, None

    # ---- 0c. je antwoorden uit Telegram ----
    antwoorden: list[str] = []
    if args.telegram:
        laatste = con.execute("SELECT MAX(datum) d FROM waarde").fetchone()["d"]
        antwoorden = _verwerk_antwoorden(
            con, laatste or date.today().isoformat(),
            droog=bool(cfg["challenger"].get("droogtest")),
        )
        if antwoorden:
            cfg = modus.laad()  # een akkoord kan een instelling hebben veranderd

    # ---- 0d. een herinnering op een vaste dag (proefweek) ----
    herinnering = (cfg.get("proefweek") or {}).get("herinnering") if cfg["modus"] == "proefweek" else None
    if herinnering and str(herinnering["datum"]) == vandaag_utc.isoformat():
        sleutel = f"herinnering_{herinnering['datum']}"
        if database.lees_instelling(con, sleutel) is None:
            tekst = str(herinnering["tekst"]).strip()
            schatting = _schatting_echte_meting(con)
            if schatting:
                tekst += f"\n\n{schatting}"
            args.berichten.append(tekst)
            database.zet_instelling(con, sleutel, "verstuurd")
            print(f"\nHerinnering klaargezet: {tekst[:80]}...")

    # ---- 1. koersen ----
    if args.geen_data:
        print("\nKoersen ophalen overgeslagen.")
    else:
        print("\nKoersen ophalen...")
        if not _haal_data_op(args.jaren, waarschuwingen, db=db):
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
        met_challengers=True,
    )
    print(f"{len(motor.deelnemers)} portefeuilles.")

    # Begin bij de oudste dag die nog niet voor iedereen af is. Een portefeuille
    # die nog nooit gedraaid heeft (zoals een nieuwe challenger) begint op zijn
    # eigen startdag, niet op de start van de hele meting.
    laatst_gedaan = [
        database.laatste_verwerkte_dag(con, d.portefeuille.naam) or d.portefeuille.gestart_op or start
        for d in motor.deelnemers
    ]
    vanaf = min(laatst_gedaan) if laatst_gedaan else start
    vanaf = max(vanaf, start)

    te_doen = [d for d in markt.datums if vanaf <= d <= tot]
    print(f"Te verwerken: {vanaf} tot en met {tot} ({len(te_doen)} dagen in de data).")

    # ---- 3b. zijn de koersen compleet? ----
    from bot import volledigheid

    problemen = volledigheid.controleer(markt, cfg, te_doen)
    if problemen:
        print("\nDE KOERSEN ZIJN NIET COMPLEET. Vannacht wordt er niet gehandeld:")
        for p in problemen:
            print(f"  {p}")
        print(
            "De volgende ronde haalt deze dag(en) vanzelf in, met complete koersen.\n"
            "Blijft een symbool wegvallen, haal het dan uit config.yaml."
        )
        tekst = overzicht.telegram_dagbericht(
            con,
            tot,
            extra=[
                *voor_aan,
                "Vannacht NIET gehandeld: de koersen zijn niet compleet.",
                *[f"  {p}" for p in problemen[:8]],
                "De volgende ronde haalt dit vanzelf in. Blijft een symbool wegvallen, "
                "haal het dan uit config.yaml.",
            ],
        )
        con.close()
        return 0, tekst

    # ---- 4. draaien ----
    if te_doen:
        motor.draai_periode(vanaf, tot, stil=True)
        if motor.overgeslagen_dagen:
            print(f"{motor.overgeslagen_dagen} dagstanden waren al verwerkt en overgeslagen.")
    else:
        print("Geen nieuwe koersdag voor de motor.")

    # ---- 5. de AI-analist ----
    ai_regels: list[str] = []
    if not args.geen_ai and not _ai_mag(args):
        print(
            "\nAI-analist overgeslagen: dit is niet de echte database. De AI kost echt geld;\n"
            "gebruik --ai-op-kopie als je dat hier toch wilt."
        )
        ai_regels = ["AI-analist overgeslagen (niet de echte database)."]
    elif not args.geen_ai:
        ai_regels = _ai_ronde(
            con, cfg, markt, tot, nieuw_nieuws=not args.geen_data, ingehaald=len(te_doen)
        )

    # ---- 6. het weekrapport ----
    from bot import weekrapport

    extra_dag = (cfg.get("proefweek") or {}).get("extra_weekrapport_op") if cfg["modus"] == "proefweek" else None
    extra_rapport = bool(extra_dag) and str(extra_dag) == vandaag_utc.isoformat()
    if args.weekrapport == "ja" or (
        args.weekrapport == "auto" and (extra_rapport or weekrapport.is_nodig(con, cfg, tot))
    ):
        print("\nWeekrapport")
        print("-" * 60)
        client = None
        if conf.sleutel("ANTHROPIC_API_KEY") and not args.geen_ai and _ai_mag(args):
            from bot.ai.ronde import maak_client

            client = maak_client(con, cfg)
        week = weekrapport.maak(con, cfg, tot, client=client)
        if week.al_gemaakt:
            print(f"Er was al een weekrapport over {tot}; niet opnieuw gemaakt (en niet opnieuw betaald).")
        else:
            print(week.tekst)
            args.berichten.append(week.tekst)
            args.berichten.extend(week.extra_berichten)

    # ---- 7. het dagbericht ----
    print("\n" + "=" * 60)
    print(overzicht.dagbericht(con, tot))
    print("=" * 60)

    extra = list(voor_aan) + list(antwoorden) + list(ai_regels)
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
    p.add_argument(
        "--ai-op-kopie", action="store_true",
        help="de AI ook laten draaien (en betalen) als --db naar een kopie wijst",
    )
    p.add_argument(
        "--weekrapport", choices=["auto", "ja", "nee"], default="auto",
        help="auto: alleen op de rapportdag uit config.yaml",
    )
    p.add_argument(
        "--omschakelen", choices=["auto", "ja"], default="auto",
        help="ja: nu van de proefweek naar de echte meting (gebeurt anders vanzelf)",
    )
    args = p.parse_args()
    args.label = ""

    from bot import telegram

    def label(tekst: str) -> str:
        return f"{args.label}\n{tekst}" if args.label else tekst

    try:
        code, tekst = ronde(args)
    except Exception as fout:
        traceback.print_exc()
        if args.telegram and telegram.is_ingesteld():
            try:
                telegram.stuur(label(
                    "Paper trading: de dagelijkse ronde is mislukt.\n\n"
                    f"{type(fout).__name__}: {str(fout)[:500]}\n\n"
                    "De database is niet half bijgewerkt: elke dag gaat in een keer of "
                    "niet. Kijk in de logboeken van GitHub Actions voor details."
                ))
            except telegram.TelegramFout as tfout:
                print(f"Ook de foutmelding naar Telegram lukte niet: {tfout}")
        return 1

    if args.telegram:
        if not telegram.is_ingesteld():
            print("\nTelegram overgeslagen: TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt.")
        elif code != 0 or tekst is None:
            try:
                telegram.stuur(label(f"Paper trading: de dagelijkse ronde stopte (code {code}). Zie de logboeken."))
            except telegram.TelegramFout as tfout:
                print(f"Telegram lukte niet: {tfout}")
        else:
            for bericht in [tekst, *getattr(args, "berichten", [])]:
                try:
                    delen = telegram.stuur(label(bericht), stil=True)
                    print(f"\nTelegram: bericht verstuurd ({delen} deel/delen).")
                except telegram.TelegramFout as tfout:
                    print(f"\nTelegram lukte niet: {tfout}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
