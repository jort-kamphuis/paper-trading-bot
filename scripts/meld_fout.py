"""Stuur een korte foutmelding naar Telegram als de nachtelijke ronde vastloopt.

Dit script gebruikt alleen wat standaard in Python zit. Het moet ook werken als
het installeren van de pakketten zelf de fout was.

Gebruik (door GitHub Actions):
    python scripts/meld_fout.py "omschrijving" "link naar het logboek"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("Geen Telegram-gegevens; foutmelding niet verstuurd.")
        return 0

    omschrijving = sys.argv[1] if len(sys.argv) > 1 else "onbekende fout"
    link = sys.argv[2] if len(sys.argv) > 2 else ""
    tekst = f"Paper trading: de nachtelijke ronde is vastgelopen.\n\n{omschrijving}"
    if link:
        tekst += f"\n\nLogboek: {link}"

    verzoek = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": tekst}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(verzoek, timeout=20) as antwoord:
            antwoord.read()
    except Exception as fout:
        # Nooit de sleutel tonen: die zit in het adres.
        print(f"Foutmelding naar Telegram lukte niet: {type(fout).__name__}: {str(fout).replace(token, '<sleutel>')}")
        return 0
    print("Foutmelding naar Telegram verstuurd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
