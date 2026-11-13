"""Nu van de proefweek naar de echte meting, in plaats van vanzelf op 20 november.

Je hebt dit normaal niet nodig: de eerste ronde na de proefweek schakelt vanzelf
om. Wil je het eerder, of ging er iets mis, dan doet dit ene commando het:

    python scripts/omschakelen.py

Het start de nachtronde op GitHub met 'omschakelen = ja'. Het omschakelen
gebeurt dus op GitHub, waar de echte database staat, en niet op je laptop.
Anders zou je lokale database een andere worden dan die op GitHub. Het gebeurt
nooit twee keer: is de echte meting al gestart, dan doet de ronde niets extra.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf


def _repository() -> str:
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
    antwoord = input(
        f"Dit start de echte meting nu, op GitHub ({repo}). De proefweek stopt dan.\n"
        f"Typ 'ja' om door te gaan: "
    ).strip().lower()
    if antwoord != "ja":
        print("Niets gedaan.")
        return 1

    token = subprocess.run(["gh", "auth", "token", "-u", eigenaar], capture_output=True, text=True)
    if token.returncode != 0 or not token.stdout.strip():
        print(f"'gh' is niet ingelogd als {eigenaar}. Log in met: gh auth login")
        return 1
    omgeving = {**os.environ, "GH_TOKEN": token.stdout.strip()}
    uitkomst = subprocess.run(
        ["gh", "workflow", "run", "dagelijks.yml", "--repo", repo, "-f", "omschakelen=ja"],
        capture_output=True, text=True, env=omgeving,
    )
    if uitkomst.returncode != 0:
        print(f"Starten lukte niet: {uitkomst.stderr.strip()[:300]}")
        return 1
    print("Gestart. Over ongeveer een uur krijg je in Telegram bericht dat de echte meting loopt.")
    print(f"Volgen kan op https://github.com/{repo}/actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
