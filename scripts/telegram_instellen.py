"""Telegram instellen: je chat-id vinden en een testbericht sturen.

Vooraf:
1. Maak in Telegram een bot aan via @BotFather (zie README) en zet de sleutel
   die je krijgt in .env, achter TELEGRAM_BOT_TOKEN=.
2. Stuur je nieuwe bot in Telegram zelf een berichtje, bijvoorbeeld "hoi".
   Pas daarna kan de bot jouw chat vinden.

Dan:
    python scripts/telegram_instellen.py

Het script zoekt je chat-id op, vraagt of het die in .env mag zetten, en
stuurt een testbericht. De sleutel zelf wordt nergens getoond.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import telegram

ENV = conf.PROJECT / ".env"


def _zet_in_env(naam: str, waarde: str) -> None:
    """Vul een waarde in .env in, zonder de rest van het bestand aan te raken."""
    regels = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    gevonden = False
    for i, regel in enumerate(regels):
        if regel.split("=", 1)[0].strip() == naam:
            regels[i] = f"{naam}={waarde}"
            gevonden = True
    if not gevonden:
        regels.append(f"{naam}={waarde}")
    ENV.write_text("\n".join(regels) + "\n", encoding="utf-8")


def main() -> int:
    token = conf.sleutel("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Geen TELEGRAM_BOT_TOKEN in .env. Maak eerst een bot aan via @BotFather.")
        return 1

    try:
        bot = telegram.wie_ben_ik(token)
    except telegram.TelegramFout as fout:
        print(f"De sleutel werkt niet: {fout}")
        return 1
    print(f"Sleutel werkt. Je bot heet @{bot.get('username')}.")

    chat = conf.sleutel("TELEGRAM_CHAT_ID")
    if chat:
        print(f"Er staat al een chat-id in .env: {chat}")
    else:
        try:
            chats = telegram.recente_chats(token)
        except telegram.TelegramFout as fout:
            print(f"Kon de berichten niet ophalen: {fout}")
            return 1
        if not chats:
            print(
                f"\nNog geen berichten gevonden. Open Telegram, zoek @{bot.get('username')},\n"
                f"stuur hem een berichtje (bijvoorbeeld 'hoi') en draai dit script opnieuw."
            )
            return 1
        if len(chats) > 1:
            print("Meerdere chats gevonden:")
            for chat_id, naam in chats:
                print(f"  {chat_id}  {naam}")
            print("Zet zelf de juiste in .env, achter TELEGRAM_CHAT_ID=, en draai dit script opnieuw.")
            return 1
        chat_id, naam = chats[0]
        print(f"Chat gevonden: {naam} ({chat_id}).")
        antwoord = input("Mag ik dit chat-id in .env zetten? Typ 'ja': ").strip().lower()
        if antwoord != "ja":
            print("Niets aangepast.")
            return 1
        _zet_in_env("TELEGRAM_CHAT_ID", chat_id)
        import os

        os.environ["TELEGRAM_CHAT_ID"] = chat_id
        print("In .env gezet.")

    try:
        telegram.stuur(
            "Hallo! Dit is je paper trading-bot. Als je dit leest, werkt Telegram.\n\n"
            "Vanaf nu krijg je hier elke nacht een kort bericht met de stand."
        )
    except telegram.TelegramFout as fout:
        print(f"Testbericht lukte niet: {fout}")
        return 1
    print("Testbericht verstuurd. Kijk in Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
