"""De belangrijkste test: er zit nergens code in die met echt geld kan handelen.

Deze test leest alle Python-bestanden van het project en zoekt naar opdrachten
waarmee je bij een broker of beurs een order plaatst of geld verplaatst. Als
iemand (of een AI) dat ooit toevoegt, valt deze test om.
"""

from __future__ import annotations

from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# Opdrachten waarmee je bij een echte beurs handelt of geld verplaatst.
VERBODEN = [
    "create_order",
    "createOrder",
    "create_market_buy_order",
    "create_market_sell_order",
    "create_limit_order",
    "place_order",
    "submit_order",
    "cancel_order",
    "withdraw",
    "transfer",
    "apiKey",
    "api_key=",
    "secret=",
    "privateKey",
    "private_key",
    "alpaca",
    "interactivebrokers",
    "ib_insync",
]

# Deze bestanden mogen de woorden noemen, want ze gaan er juist over.
UITZONDERINGEN = {"test_geen_echt_geld.py"}


def python_bestanden() -> list[Path]:
    uit: list[Path] = []
    for map_naam in ("bot", "scripts", "dashboard", "tests"):
        pad = PROJECT / map_naam
        if pad.exists():
            uit.extend(p for p in pad.rglob("*.py") if ".venv" not in p.parts)
    return uit


def test_geen_order_opdrachten():
    gevonden: list[str] = []
    for bestand in python_bestanden():
        if bestand.name in UITZONDERINGEN:
            continue
        tekst = bestand.read_text(encoding="utf-8")
        for regelnr, regel in enumerate(tekst.splitlines(), 1):
            # Commentaar mag het woord noemen.
            zonder_commentaar = regel.split("#", 1)[0]
            for woord in VERBODEN:
                if woord in zonder_commentaar:
                    gevonden.append(f"{bestand.relative_to(PROJECT)}:{regelnr}: {woord}")
    assert not gevonden, (
        "Er staat code in die met echt geld zou kunnen handelen:\n" + "\n".join(gevonden)
    )


TOEGESTANE_SLEUTELS = {"ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def test_alleen_bekende_sleutels_worden_gelezen():
    """De bot leest alleen de sleutel voor Claude en die voor Telegram.

    Komt er ooit een sleutel bij voor een beurs of een broker, dan valt deze
    test om. Dat is de bedoeling.
    """
    import re

    gelezen: set[str] = set()
    for bestand in python_bestanden():
        if bestand.name in UITZONDERINGEN:
            continue
        tekst = bestand.read_text(encoding="utf-8")
        gelezen.update(re.findall(r"sleutel\(\s*[\"']([A-Z0-9_]+)[\"']", tekst))
        gelezen.update(re.findall(r"environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)[\"']", tekst))
        gelezen.update(re.findall(r"environ\[\s*[\"']([A-Z0-9_]+)[\"']\s*\]", tekst))
    onbekend = gelezen - TOEGESTANE_SLEUTELS
    assert not onbekend, f"de bot leest een onbekende sleutel: {sorted(onbekend)}"


def test_env_voorbeeld_bevat_alleen_bekende_sleutels():
    regels = (PROJECT / ".env.example").read_text(encoding="utf-8").splitlines()
    namen = {r.split("=", 1)[0].strip() for r in regels if "=" in r and not r.lstrip().startswith("#")}
    assert namen <= TOEGESTANE_SLEUTELS, f"onbekende sleutel in .env.example: {namen - TOEGESTANE_SLEUTELS}"


def test_ccxt_wordt_zonder_sleutels_gebruikt():
    """ccxt mag alleen koersen ophalen, dus zonder sleutels aangemaakt worden."""
    bron = (PROJECT / "bot" / "data" / "koersen.py").read_text(encoding="utf-8")
    assert "enableRateLimit" in bron
    for woord in ("apiKey", "secret", "password", "uid"):
        assert f'"{woord}"' not in bron, f"ccxt krijgt {woord} mee; dat mag niet"


def test_alleen_koers_opdrachten_van_ccxt():
    """Van ccxt gebruiken we alleen leesopdrachten."""
    bron = (PROJECT / "bot" / "data" / "koersen.py").read_text(encoding="utf-8")
    toegestaan = {"fetch_ohlcv", "load_markets"}
    gebruikt = {
        woord
        for woord in bron.replace("(", " ").replace(".", " ").split()
        if woord.startswith("fetch_") or woord.startswith("create_")
    }
    extra = gebruikt - toegestaan
    assert not extra, f"onverwachte ccxt-opdrachten: {extra}"
