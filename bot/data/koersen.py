"""Dagkoersen ophalen: aandelen en ETF's via yfinance, crypto via ccxt.

Alleen publieke data. Er wordt nergens een sleutel gebruikt en nergens een
order geplaatst: ccxt wordt uitsluitend gebruikt om koersen op te halen.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

# Aandelen en ETF's op Amerikaanse beurzen noteren in dollars.
VALUTA_AANDEEL = "USD"
# De cryptoparen staan in USDT. Zie README: we behandelen 1 USDT als 1 USD.
VALUTA_CRYPTO = "USD"


def eerste_onaffe_dag_aandelen(nu: datetime | None = None) -> str:
    """De eerste beursdag waarvan de slotkoers nog niet vaststaat.

    De Amerikaanse beurs sluit om 16:00 in New York. Tot 17:00 daar (een uur
    marge voor late correcties) is de dag van vandaag dus nog niet af.
    """
    from zoneinfo import ZoneInfo

    ny = (nu or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    if ny.hour >= 17:
        return (ny.date() + timedelta(days=1)).isoformat()
    return ny.date().isoformat()


def eerste_onaffe_dag_crypto(nu: datetime | None = None) -> str:
    """Een cryptodag loopt van 00:00 tot 24:00 UTC. Vandaag is dus nog niet af."""
    return (nu or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _naar_datumtekst(waarde) -> str:
    """Maak van een tijdstempel een datum in de vorm JJJJ-MM-DD."""
    if isinstance(waarde, str):
        return waarde[:10]
    if isinstance(waarde, (datetime, pd.Timestamp)):
        return waarde.date().isoformat()
    if isinstance(waarde, date):
        return waarde.isoformat()
    raise TypeError(f"onbekend datumtype: {type(waarde)!r}")


def haal_aandelen(
    symbolen: list[str], start: str, eind: str | None = None
) -> list[dict]:
    """Haal dagkoersen op voor aandelen en ETF's.

    Geeft een lijst met rijen voor de database terug. 'start' en 'eind' zijn
    datums in de vorm JJJJ-MM-DD; 'eind' is niet inbegrepen (zoals yfinance doet).
    """
    if not symbolen:
        return []

    ruw = yf.download(
        symbolen,
        start=start,
        end=eind,
        interval="1d",
        auto_adjust=False,
        # Ook dividend en splitsingen. 'Close' is wel aangepast voor
        # splitsingen (achteraf!), niet voor dividend; zie database.py.
        actions=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if ruw is None or len(ruw) == 0:
        return []

    rijen: list[dict] = []
    heeft_multi = isinstance(ruw.columns, pd.MultiIndex)
    # Een dag die nog bezig is, heeft nog geen echte slotkoers. Die laten we weg.
    onaf_vanaf = eerste_onaffe_dag_aandelen()

    for symbool in symbolen:
        if heeft_multi:
            velden = {}
            for veld in VELDEN:
                if (veld, symbool) in ruw.columns:
                    velden[veld] = ruw[(veld, symbool)]
            if len([v for v in ("Open", "High", "Low", "Close") if v in velden]) < 4:
                continue
            deel = pd.DataFrame(velden)
        else:
            # Bij een enkel symbool geeft yfinance gewone kolommen terug.
            if len(symbolen) != 1:
                continue
            deel = ruw[[v for v in VELDEN if v in ruw.columns]].copy()

        rijen.extend(_rijen_uit(symbool, deel, onaf_vanaf))
    return rijen


VELDEN = ("Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits")


def _getal_of_niets(waarde) -> float | None:
    if waarde is None or pd.isna(waarde):
        return None
    getal = float(waarde)
    return getal if getal != 0 else None


def _rijen_uit(symbool: str, deel: pd.DataFrame, onaf_vanaf: str) -> list[dict]:
    """Van een tabel met koersen van een symbool naar rijen voor de database."""
    rijen: list[dict] = []
    deel = deel.dropna(subset=["Open", "Close"])
    for stempel, rij in deel.iterrows():
        datum = _naar_datumtekst(stempel)
        if datum >= onaf_vanaf:
            continue
        rijen.append(
            {
                "symbool": symbool,
                "datum": datum,
                "open": float(rij["Open"]),
                "hoog": float(rij["High"]) if pd.notna(rij.get("High")) else None,
                "laag": float(rij["Low"]) if pd.notna(rij.get("Low")) else None,
                "slot": float(rij["Close"]),
                "volume": float(rij["Volume"]) if pd.notna(rij.get("Volume")) else None,
                "valuta": VALUTA_AANDEEL,
                "bron": "yfinance",
                "dividend": _getal_of_niets(rij.get("Dividends")),
                "splitsing": _getal_of_niets(rij.get("Stock Splits")),
            }
        )
    return rijen


def haal_een_aandeel(symbool: str, start: str, eind: str | None = None) -> list[dict]:
    """Haal een enkel aandeel op, los van de rest."""
    ruw = yf.Ticker(symbool).history(start=start, end=eind, interval="1d", auto_adjust=False)
    if ruw is None or len(ruw) == 0:
        return []
    return _rijen_uit(symbool, ruw, eerste_onaffe_dag_aandelen())


# Pauzes tussen de herkansingen, in seconden. Yahoo weigert soms tijdelijk
# verzoeken van servers die veel vragen; even wachten helpt dan meestal.
HERKANSING_PAUZES = (10, 30, 60)


def haal_aandelen_met_herkansing(
    symbolen: list[str],
    start: str,
    eind: str | None = None,
    pauzes: tuple[int, ...] = HERKANSING_PAUZES,
    slaap=time.sleep,
) -> tuple[list[dict], dict[str, str]]:
    """Haal alle aandelen op en probeer ontbrekende nog een paar keer los.

    Geeft de rijen terug en per symbool dat na alle pogingen nog ontbreekt de
    reden, zoals Yahoo die gaf.
    """
    rijen = haal_aandelen(symbolen, start=start, eind=eind)
    gevonden = {r["symbool"] for r in rijen}
    ontbreekt = [s for s in symbolen if s not in gevonden]
    redenen: dict[str, str] = {}

    for symbool in ontbreekt:
        reden = "geen koersen ontvangen"
        for pauze in pauzes:
            slaap(pauze)
            try:
                extra = haal_een_aandeel(symbool, start=start, eind=eind)
            except Exception as fout:
                reden = f"{type(fout).__name__}: {str(fout)[:150]}"
                continue
            if extra:
                rijen.extend(extra)
                print(f"  {symbool}: bij herkansing alsnog opgehaald")
                break
        else:
            redenen[symbool] = reden
    return rijen, redenen


def _beurs(naam: str):
    """Maak een ccxt-beurs aan, zonder sleutels, dus alleen publieke data."""
    import ccxt

    klasse = getattr(ccxt, naam, None)
    if klasse is None:
        raise ValueError(f"ccxt kent de beurs '{naam}' niet")
    return klasse({"enableRateLimit": True})


def haal_crypto(
    paren: list[str], start: str, beurzen: list[str] | None = None
) -> tuple[list[dict], dict[str, str]]:
    """Haal dagkoersen op voor cryptoparen.

    Probeert de beurzen in de opgegeven volgorde en gebruikt de eerste die
    antwoordt. Geeft de rijen terug plus per paar de beurs die het leverde.
    """
    beurzen = beurzen or ["kraken", "okx", "binance"]
    if not paren:
        return [], {}

    sinds_ms = int(
        datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    rijen: list[dict] = []
    gebruikt: dict[str, str] = {}
    # De beurs geeft ook de dag die nog bezig is, met als "slotkoers" de koers
    # van dit moment. Die is niet af, dus die laten we weg.
    onaf_vanaf = eerste_onaffe_dag_crypto()

    for paar in paren:
        for beursnaam in beurzen:
            try:
                beurs = _beurs(beursnaam)
                bars = _haal_alle_bars(beurs, paar, sinds_ms)
            except Exception as fout:  # een beurs kan weigeren of het paar niet hebben
                print(f"  {paar} via {beursnaam}: niet gelukt ({type(fout).__name__}: {str(fout)[:80]})")
                continue
            if not bars:
                continue
            for tijd, o, h, l, c, v in bars:
                datum = datetime.fromtimestamp(tijd / 1000, tz=timezone.utc).date().isoformat()
                if datum >= onaf_vanaf:
                    continue
                rijen.append(
                    {
                        "symbool": paar,
                        "datum": datum,
                        "open": float(o),
                        "hoog": float(h),
                        "laag": float(l),
                        "slot": float(c),
                        "volume": float(v) if v is not None else None,
                        "valuta": VALUTA_CRYPTO,
                        "bron": f"ccxt:{beursnaam}",
                    }
                )
            gebruikt[paar] = beursnaam
            break
        else:
            print(f"  {paar}: geen enkele beurs gaf data")
    return rijen, gebruikt


MAX_PAGINAS = 40


def _haal_alle_bars(beurs, paar: str, sinds_ms: int, limiet: int = 1000) -> list[list]:
    """Haal dagbars op en blader door tot vandaag.

    Elke beurs geeft een ander aantal dagen per keer (Kraken 720, OKX 300). We
    bladeren dus door tot er niets nieuws meer komt of tot we bij vandaag zijn.
    Stoppen omdat een pagina "klein" was, is fout: dan mis je bij een beurs met
    kleine pagina's juist de meest recente dagen.
    """
    alles: list[list] = []
    cursor = sinds_ms
    dag_ms = 24 * 60 * 60 * 1000
    nu_ms = int(time.time() * 1000)

    for _ in range(MAX_PAGINAS):
        if cursor >= nu_ms:
            break
        bars = beurs.fetch_ohlcv(paar, timeframe="1d", since=cursor, limit=limiet)
        if not bars:
            break
        nieuw = [b for b in bars if b[0] >= cursor]
        if not nieuw:
            break
        alles.extend(nieuw)
        laatste = nieuw[-1][0]
        if laatste <= cursor:
            break
        cursor = laatste + dag_ms

    # dubbele dagen eruit, op datum sorteren
    per_dag = {b[0]: b for b in alles}
    return [per_dag[k] for k in sorted(per_dag)]


def standaard_start(jaren: int = 3) -> str:
    """Begin van de geschiedenis die we standaard ophalen."""
    return (date.today() - timedelta(days=int(365.25 * jaren))).isoformat()
