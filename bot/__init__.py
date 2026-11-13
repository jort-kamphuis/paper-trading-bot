"""Paper trading-bot. Alles met nep-geld; geen enkele koppeling met een broker."""

import sys

# De Windows-terminal gebruikt standaard een oude tekenset, waardoor letters als
# 'ï' en 'ë' als rare tekens verschijnen. Op GitHub (Linux) speelt dit niet.
for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

__all__ = ["config", "database", "portefeuille", "statistiek", "motor"]
