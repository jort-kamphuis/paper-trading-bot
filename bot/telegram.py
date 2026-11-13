"""Berichten naar Telegram sturen.

Alleen het versturen van een bericht naar jouw eigen chat. De bot leest hier
geen opdrachten (dat komt in stap 5, voor het akkoord op een verbetering).

De sleutel (TELEGRAM_BOT_TOKEN) zit in het internetadres van elke aanroep. Een
foutmelding van de verbinding bevat dat adres vaak letterlijk. Omdat deze
repository openbaar is en de logboeken van GitHub ook, halen we de sleutel uit
elke foutmelding voordat die ergens wordt getoond.
"""

from __future__ import annotations

import requests

from bot import config as conf

API = "https://api.telegram.org/bot{token}/{methode}"
# Telegram weigert berichten langer dan 4096 tekens. We blijven er ruim onder.
MAX_LENGTE = 4000


class TelegramFout(Exception):
    pass


def _zonder_sleutel(tekst: str, token: str | None) -> str:
    """Haal de sleutel uit een tekst, zodat hij nooit in een logboek belandt."""
    if token:
        tekst = tekst.replace(token, "<sleutel>")
    return tekst


def is_ingesteld() -> bool:
    return bool(conf.sleutel("TELEGRAM_BOT_TOKEN") and conf.sleutel("TELEGRAM_CHAT_ID"))


def deel_op(tekst: str, max_lengte: int = MAX_LENGTE) -> list[str]:
    """Knip een lange tekst in stukken, bij voorkeur tussen twee regels."""
    if len(tekst) <= max_lengte:
        return [tekst]
    stukken: list[str] = []
    huidig = ""
    for regel in tekst.split("\n"):
        while len(regel) > max_lengte:  # een enkele regel die zelf te lang is
            if huidig:
                stukken.append(huidig)
                huidig = ""
            stukken.append(regel[:max_lengte])
            regel = regel[max_lengte:]
        kandidaat = f"{huidig}\n{regel}" if huidig else regel
        if len(kandidaat) > max_lengte:
            stukken.append(huidig)
            huidig = regel
        else:
            huidig = kandidaat
    if huidig:
        stukken.append(huidig)
    return stukken


def _roep_aan(token: str, methode: str, gegevens: dict | None = None, timeout: int = 20) -> dict:
    url = API.format(token=token, methode=methode)
    try:
        antwoord = requests.post(url, json=gegevens or {}, timeout=timeout)
    except requests.RequestException as fout:
        raise TelegramFout(_zonder_sleutel(f"{type(fout).__name__}: {fout}", token)) from None
    try:
        inhoud = antwoord.json()
    except ValueError:
        raise TelegramFout(f"Telegram gaf geen leesbaar antwoord (status {antwoord.status_code})") from None
    if not inhoud.get("ok"):
        beschrijving = inhoud.get("description", "onbekende fout")
        raise TelegramFout(_zonder_sleutel(f"Telegram weigerde: {beschrijving}", token))
    return inhoud


def dashboard_url() -> str | None:
    try:
        return (conf.laad().get("dashboard") or {}).get("url") or None
    except Exception:  # pragma: no cover  (een bericht mag nooit mislukken om de link)
        return None


def met_dashboardlink(tekst: str) -> str:
    """Zet de link naar het dashboard onder elk bericht."""
    url = dashboard_url()
    if not url or url in tekst:
        return tekst
    return f"{tekst.rstrip()}\n\nDashboard: {url}"


def stuur(tekst: str, stil: bool = False) -> int:
    """Stuur een bericht naar je eigen chat. Geeft het aantal verstuurde delen terug.

    'stil' betekent: zonder geluid. Handig voor het dagbericht, dat 's nachts komt.
    Gewone tekst, geen opmaak: dan kan geen enkel teken het bericht laten mislukken.
    """
    token = conf.sleutel("TELEGRAM_BOT_TOKEN")
    chat = conf.sleutel("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise TelegramFout("TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt")
    delen = deel_op(met_dashboardlink(tekst))
    for i, deel in enumerate(delen, 1):
        kop = f"({i}/{len(delen)})\n" if len(delen) > 1 else ""
        _roep_aan(
            token,
            "sendMessage",
            {"chat_id": chat, "text": kop + deel, "disable_notification": stil},
        )
    return len(delen)


def wie_ben_ik(token: str) -> dict:
    """De naam van de bot, om te controleren dat de sleutel klopt."""
    return _roep_aan(token, "getMe")["result"]


def lees_berichten(token: str, chat_id: str, offset: int | None) -> tuple[list[dict], int | None]:
    """Haal nieuwe berichten op die uit jouw eigen chat komen.

    Berichten uit andere chats worden genegeerd: alleen jij kunt de bot iets
    laten doen. Geeft de berichten terug ({tekst, wanneer}) en de offset voor
    de volgende keer. Telegram bewaart onbevestigde berichten ongeveer een dag.
    """
    gegevens: dict = {"timeout": 0, "allowed_updates": ["message"]}
    if offset is not None:
        gegevens["offset"] = offset
    resultaat = _roep_aan(token, "getUpdates", gegevens)["result"]
    berichten: list[dict] = []
    volgende = offset
    for update in resultaat:
        volgende = max(volgende or 0, int(update["update_id"]) + 1)
        bericht = update.get("message") or {}
        if str((bericht.get("chat") or {}).get("id")) != str(chat_id):
            continue
        tekst = bericht.get("text")
        if not tekst:
            continue
        from datetime import datetime, timezone

        wanneer = datetime.fromtimestamp(int(bericht.get("date", 0)), timezone.utc)
        berichten.append({"tekst": tekst, "wanneer": wanneer.strftime("%Y-%m-%d %H:%M UTC")})
    return berichten, volgende


def bevestig(token: str, offset: int) -> None:
    """Laat Telegram weten dat alles tot 'offset' verwerkt is, zodat het niet terugkomt."""
    _roep_aan(token, "getUpdates", {"offset": offset, "timeout": 0, "limit": 1})


def recente_chats(token: str) -> list[tuple[str, str]]:
    """De chats die de bot recent een bericht hebben gestuurd: (chat_id, naam)."""
    resultaat = _roep_aan(token, "getUpdates", {"timeout": 0})["result"]
    gezien: dict[str, str] = {}
    for update in resultaat:
        bericht = update.get("message") or update.get("edited_message") or {}
        chat = bericht.get("chat") or {}
        if "id" in chat:
            naam = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
            gezien[str(chat["id"])] = naam
    return list(gezien.items())
