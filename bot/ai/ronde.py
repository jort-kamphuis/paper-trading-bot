"""De dagelijkse denkronde van de AI-analist.

Dit draait na de gewone dagronde, alleen voor de nieuwste dag. Op dat moment
staan de posities en de kas al goed in de database, dus de risk manager weet
precies wat er wel en niet kan.

Waarom alleen de nieuwste dag? Haalt de bot een paar gemiste dagen in, dan is
het nieuws van toen niet meer op te halen: yfinance geeft alleen de nieuwste
berichten. Een beslissing voor drie dagen geleden met het nieuws van vandaag
zou vooruitkijken zijn. Die dagen slaat de AI-analist dus over, en dat staat in
het logboek.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bot.ai.analist import AIAnalist, Besluit
from bot.ai.budget import Budget, BudgetOp
from bot.ai.client import ClaudeClient
from bot.markt import Markt
from bot.portefeuille import Portefeuille

# Na zoveel fouten achter elkaar stoppen we. Meestal is er dan iets structureel
# mis, zoals een verkeerde sleutel, en heeft doorgaan geen zin.
MAX_FOUTEN_ACHTER_ELKAAR = 3


@dataclass
class Uitkomst:
    portefeuille: str
    datum: str
    besluiten: list[Besluit] = field(default_factory=list)
    al_gedaan: int = 0
    fouten: list[str] = field(default_factory=list)
    gestopt: str | None = None
    kosten_usd: float = 0.0

    @property
    def orders(self) -> list[Besluit]:
        return [b for b in self.besluiten if b.actie in ("KOOP", "VERKOOP")]


def _bestaand_besluit(con: sqlite3.Connection, pf: str, symbool: str, datum: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM ai_besluiten WHERE portefeuille=? AND symbool=? AND datum=?",
            (pf, symbool, datum),
        ).fetchone()
        is not None
    )


def _bewaar_besluit(
    con: sqlite3.Connection, pf: str, besluit: Besluit, nieuws_tot: str
) -> None:
    koppen = [
        {
            "gepubliceerd": k.get("gepubliceerd"),
            "titel": k.get("titel"),
            "bron": k.get("bron"),
            "url": k.get("url"),
        }
        for k in besluit.koppen
    ]
    con.execute(
        """INSERT INTO ai_besluiten
           (portefeuille, symbool, datum, actie, onderbouwing, zekerheid, stappen, koppen,
            koppen_gezien, kosten_usd, overgeslagen, nieuws_tot, gemaakt_op)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(portefeuille, symbool, datum) DO NOTHING""",
        (
            pf,
            besluit.symbool,
            besluit.datum,
            besluit.actie,
            besluit.onderbouwing,
            besluit.zekerheid,
            json.dumps(besluit.stappen, ensure_ascii=False),
            json.dumps(koppen, ensure_ascii=False),
            besluit.koppen_gezien,
            besluit.kosten_usd,
            besluit.overgeslagen,
            nieuws_tot,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _zet_order_klaar(con: sqlite3.Connection, pf: str, besluit: Besluit) -> None:
    """Zet het besluit in de wachtrij. De motor voert het de volgende handelsdag uit."""
    con.execute(
        """INSERT INTO wachtrij (portefeuille, symbool, actie, reden, zekerheid, besluit_datum)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(portefeuille, symbool, actie, besluit_datum) DO NOTHING""",
        (pf, besluit.symbool, besluit.actie, besluit.onderbouwing, besluit.zekerheid, besluit.datum),
    )


def nieuws_grens(symbool: str, datum: str, nu: datetime | None = None) -> str:
    """Tot welk moment de AI nieuws mag zien voor een besluit over 'datum'.

    Een besluit van dag D wordt uitgevoerd op de openingskoers van de volgende
    handelsdag. Nieuws dat na die opening verscheen, kon de koers op dat moment
    nog niet beïnvloeden. Wie het toch meeweegt, kijkt vooruit. De grens is
    dus het vroegste van: nu, of de opening waarop het besluit wordt uitgevoerd.

    * Crypto: de volgende dag begint om 00:00 UTC. De bot draait daarna (rond
      00:30 UTC, soms later), dus zonder deze grens zou nieuws van na middernacht
      meetellen terwijl de order al op de middernachtkoers afgaat.
    * Aandelen: de Amerikaanse beurs opent op zijn vroegst om 13:30 UTC (in de
      zomer). Dat ligt altijd na het moment waarop de bot draait.
    """
    from datetime import timedelta

    from bot.config import is_crypto

    nu = nu or datetime.now(timezone.utc)
    volgende = datetime.fromisoformat(datum).replace(tzinfo=timezone.utc) + timedelta(days=1)
    opening = volgende if is_crypto(symbool) else volgende.replace(hour=13, minute=30)
    return min(nu, opening).isoformat()


def denk_na(
    con: sqlite3.Connection,
    cfg: dict,
    client: ClaudeClient,
    markt: Markt,
    portefeuille: str,
    datum: str,
    symbolen: list[str],
    model_override: str | None = None,
    nieuws_tot: str | None = None,
    stil: bool = False,
) -> Uitkomst:
    """Laat de AI-analist voor een portefeuille en een dag beslissen."""
    uitkomst = Uitkomst(portefeuille=portefeuille, datum=datum)
    # 'nieuws_tot' is een extra bovengrens (handig in tests). De echte grens
    # wordt per symbool bepaald door nieuws_grens().
    plafond = datetime.fromisoformat(nieuws_tot) if nieuws_tot else None

    pf = Portefeuille.uit_database(con, portefeuille, cfg["algemeen"])
    if pf is None:
        uitkomst.gestopt = f"portefeuille {portefeuille} bestaat nog niet"
        return uitkomst

    analist = AIAnalist(con, cfg, client, model_override=model_override)
    max_per_trade = float(cfg["algemeen"]["max_per_trade_eur"])
    # Kas die nog vrij is. Na elk koopbesluit houden we er rekening mee dat dat
    # geld straks weg is, zodat de risk manager niet twee keer hetzelfde geld
    # uitgeeft.
    vrije_kas = pf.kas_eur
    fouten_achter_elkaar = 0

    for symbool in symbolen:
        if _bestaand_besluit(con, portefeuille, symbool, datum):
            uitkomst.al_gedaan += 1
            continue

        heeft_positie = symbool in pf.posities
        grens = nieuws_grens(symbool, datum, nu=plafond)
        try:
            besluit = analist.beslis(
                symbool=symbool,
                datum=datum,
                markt=markt,
                heeft_positie=heeft_positie,
                kas_eur=vrije_kas,
                max_per_trade_eur=max_per_trade,
                nieuws_tot=grens,
            )
        except BudgetOp as fout:
            # Wat al is uitgegeven, staat geboekt. Dat moet bewaard blijven.
            con.commit()
            uitkomst.gestopt = str(fout)
            break
        except Exception as fout:  # netwerk, API, onverwacht antwoord
            # Geen besluit opslaan, zodat het de volgende run opnieuw wordt
            # geprobeerd. De kosten die al gemaakt zijn, staan wel geboekt.
            con.commit()
            melding = f"{symbool}: {type(fout).__name__}: {str(fout)[:200]}"
            uitkomst.fouten.append(melding)
            if not stil:
                print(f"    fout bij {melding}")
            fouten_achter_elkaar += 1
            if fouten_achter_elkaar >= MAX_FOUTEN_ACHTER_ELKAAR:
                uitkomst.gestopt = (
                    f"{MAX_FOUTEN_ACHTER_ELKAAR} fouten achter elkaar, gestopt. "
                    f"Laatste: {melding}"
                )
                break
            continue

        fouten_achter_elkaar = 0
        _bewaar_besluit(con, portefeuille, besluit, grens)
        if besluit.actie in ("KOOP", "VERKOOP"):
            _zet_order_klaar(con, portefeuille, besluit)
            if besluit.actie == "KOOP":
                vrije_kas -= max_per_trade * (1.0 + float(cfg["algemeen"]["kosten_pct"]) / 100.0)
        # Na elk symbool vastleggen: het geld is uitgegeven, dan moet het
        # besluit ook bewaard blijven.
        con.commit()

        uitkomst.besluiten.append(besluit)
        uitkomst.kosten_usd += besluit.kosten_usd
        if not stil:
            extra = f" ({besluit.overgeslagen})" if besluit.overgeslagen else ""
            print(
                f"    {symbool:10s} {besluit.actie:8s} zekerheid {besluit.zekerheid:.2f}  "
                f"${besluit.kosten_usd:.4f}{extra}"
            )

    return uitkomst


def maak_client(con: sqlite3.Connection, cfg: dict, vandaag: str | None = None) -> ClaudeClient:
    return ClaudeClient(
        Budget(con, cfg, vandaag=vandaag),
        pauze_seconden=float(cfg.get("ai", {}).get("pauze_seconden", 0) or 0),
    )


def ai_portefeuilles(cfg: dict) -> list[tuple[str, str | None]]:
    """De AI-portefeuilles: naam en eventueel het model dat voor alle rollen geldt.

    Staat er in de instellingen een lijst 'ai.portefeuilles' (zoals in de
    proefweek), dan geldt die. Anders: ai_analist plus de controleportefeuille.
    """
    if not cfg.get("ai", {}).get("actief"):
        return []
    lijst = cfg["ai"].get("portefeuilles")
    if lijst:
        return [(str(p["naam"]), p.get("model")) for p in lijst]
    uit: list[tuple[str, str | None]] = [("ai_analist", None)]
    controle = cfg["ai"].get("controle_portefeuille", {})
    if controle.get("actief"):
        model = controle["model"]
        uit.append((f"ai_controle_{model.split('-')[1]}", model))
    return uit
