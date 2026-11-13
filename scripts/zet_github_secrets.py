"""Zet je sleutels uit .env als Secrets op GitHub, zodat de nachtelijke ronde ze heeft.

Gebruik:
    python scripts/zet_github_secrets.py

Wat het doet:
* Leest ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID uit .env.
* Zet ze met het programma 'gh' als Secret bij deze repository.
* Gebruikt daarvoor het GitHub-account dat eigenaar is van de repository
  (jort-kamphuis), ook als 'gh' normaal op een ander account staat. Het
  actieve account van 'gh' wordt niet veranderd.

De waarden worden nergens getoond. Ze gaan via een versleutelde verbinding
rechtstreeks naar GitHub. Daar kan niemand ze meer uitlezen, ook jij niet;
je kunt ze alleen vervangen.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values

from bot import config as conf

NAMEN = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]


def _repository() -> str:
    """eigenaar/naam van deze repository, uit de git-instellingen."""
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=conf.PROJECT, check=True,
    ).stdout.strip()
    gevonden = re.search(r"github\.com[:/](?:[^@/]+@)?([^/]+)/([^/.]+)", url)
    if not gevonden:
        raise SystemExit(f"Kan de repository niet herkennen uit: {url}")
    return f"{gevonden.group(1)}/{gevonden.group(2)}"


def main() -> int:
    repo = _repository()
    eigenaar = repo.split("/")[0]

    waarden = dotenv_values(conf.PROJECT / ".env")
    leeg = [n for n in NAMEN if not (waarden.get(n) or "").strip()]
    if leeg:
        print(f"Nog leeg in .env: {', '.join(leeg)}. Vul die eerst in.")
        return 1

    token = subprocess.run(
        ["gh", "auth", "token", "-u", eigenaar], capture_output=True, text=True
    )
    if token.returncode != 0 or not token.stdout.strip():
        print(f"'gh' is niet ingelogd als {eigenaar}. Log in met: gh auth login")
        return 1
    omgeving = {**os.environ, "GH_TOKEN": token.stdout.strip()}

    print(f"Secrets zetten bij {repo} (als {eigenaar}):")
    for naam in NAMEN:
        uitkomst = subprocess.run(
            ["gh", "secret", "set", naam, "--repo", repo],
            input=waarden[naam].strip(),
            capture_output=True, text=True, env=omgeving,
        )
        if uitkomst.returncode != 0:
            print(f"  {naam}: MISLUKT ({uitkomst.stderr.strip()[:200]})")
            return 1
        print(f"  {naam}: gezet")

    lijst = subprocess.run(
        ["gh", "secret", "list", "--repo", repo], capture_output=True, text=True, env=omgeving
    )
    print("\nOp GitHub staan nu (alleen de namen, de waarden zijn niet uit te lezen):")
    print(lijst.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
