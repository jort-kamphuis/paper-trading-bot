"""De verbinding met de Claude-API.

Alles wat met de API te maken heeft, staat hier bij elkaar. De rest van de bot
kent alleen `vraag()` en hoeft niets van de API te weten. Daardoor kunnen de
tests met een nep-client draaien: geen sleutel, geen internet, geen kosten.

De sleutel komt uit .env (lokaal) of uit GitHub Secrets (als de bot vanzelf
draait). Hij staat nooit in de code en nooit in git.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bot.ai.budget import Budget, bereken_kosten


@dataclass
class Antwoord:
    """Wat een rol terugkomt met."""

    rol: str
    model: str
    tekst: str
    data: dict[str, Any] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_uit: int = 0
    usd: float = 0.0
    geweigerd: bool = False
    afgekapt: bool = False

    def probleem(self) -> str | None:
        """Waarom dit antwoord onbruikbaar is, in gewone taal. None als het goed is."""
        if self.geweigerd:
            return f"{self.rol} weigerde te antwoorden"
        if self.afgekapt:
            return f"antwoord van {self.rol} afgekapt op het plafond (max_tokens te krap)"
        if not self.data:
            return f"{self.rol} gaf geen leesbaar antwoord"
        return None


class GeenSleutel(Exception):
    """Er is geen ANTHROPIC_API_KEY gevonden."""


class ClaudeClient:
    """Praat met de Claude-API en boekt elke aanroep in het budget."""

    # Hoe vaak de bibliotheek een aanroep opnieuw probeert bij een tijdelijke
    # fout, zoals "te veel verzoeken" (429) of een overbelaste server. Ze wacht
    # daar zelf steeds langer tussen.
    OPNIEUW_PROBEREN = 5

    def __init__(self, budget: Budget, pauze_seconden: float = 0.0):
        self.budget = budget
        self._client = None
        # Een vaste pauze tussen aanroepen, voor als de limieten van het
        # account krap blijken. Standaard uit.
        self.pauze_seconden = float(pauze_seconden or 0.0)
        self._vorige: float | None = None

    def _verbinding(self):
        """Maak de verbinding pas als hij echt nodig is.

        Zo kunnen de tests dit bestand gebruiken zonder dat het pakket
        anthropic geïnstalleerd hoeft te zijn.
        """
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as fout:  # pragma: no cover
            raise ImportError(
                "Het pakket 'anthropic' ontbreekt. Installeer het met:\n"
                "  .venv\\Scripts\\python.exe -m pip install anthropic"
            ) from fout

        from bot import config as conf

        # conf.sleutel leest .env in en zet de sleutel in de omgeving. De
        # bibliotheek van Anthropic pakt hem daar zelf op. De sleutel gaat dus
        # nooit door deze code heen en kan ook nergens per ongeluk in een
        # logregel of foutmelding belanden.
        if not conf.sleutel("ANTHROPIC_API_KEY"):
            raise GeenSleutel(
                "Geen ANTHROPIC_API_KEY gevonden.\n"
                "Lokaal: zet hem in het bestand .env (zie .env.example).\n"
                "Op GitHub: zet hem als Secret bij de repository."
            )
        self._client = anthropic.Anthropic(max_retries=self.OPNIEUW_PROBEREN)
        return self._client

    def _wacht_even(self) -> None:
        if not self.pauze_seconden:
            return
        import time

        if self._vorige is not None:
            over = self.pauze_seconden - (time.monotonic() - self._vorige)
            if over > 0:
                time.sleep(over)
        self._vorige = time.monotonic()

    # ---------- de enige methode die de rest van de bot gebruikt ----------

    def vraag(
        self,
        rol: str,
        model: str,
        systeem: str,
        bericht: str,
        max_tokens: int,
        schema: dict | None = None,
        denken: str = "aan",
        inspanning: str = "low",
    ) -> Antwoord:
        """Stel een vraag aan een rol en krijg het antwoord terug.

        'schema' is een JSON-schema. Staat dat erin, dan dwingt de API het
        antwoord in precies die vorm. Dat is belangrijk: de bot moet een harde
        beslissing hebben, geen verhaal waar hij nog iets uit moet vissen.
        """
        self.budget.controleer()
        client = self._verbinding()
        self._wacht_even()

        output_config: dict[str, Any] = {"effort": inspanning}
        if schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": schema,
            }

        verzoek: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": systeem,
            "messages": [{"role": "user", "content": bericht}],
            "output_config": output_config,
        }
        # Denkwerk kost uitvoertokens en dus geld. Bij het verzamelen van
        # feiten valt er niets te overwegen, dus daar zetten we het uit.
        # Claude Sonnet 5.5 staat 'uit' niet toe; daar is lage inspanning
        # de zuinigste stand en laten we het denkwerk gewoon aan.
        if denken == "uit" and "haiku" in model:
            verzoek["thinking"] = {"type": "disabled"}
        else:
            verzoek["thinking"] = {"type": "adaptive"}

        antwoord = client.messages.create(**verzoek)

        tekst = "".join(
            blok.text for blok in antwoord.content if getattr(blok, "type", "") == "text"
        )
        stopreden = getattr(antwoord, "stop_reason", None)
        geweigerd = stopreden == "refusal"
        afgekapt = stopreden == "max_tokens"

        gebruik = antwoord.usage
        kosten = bereken_kosten(
            self.budget.prijzen,
            model,
            int(getattr(gebruik, "input_tokens", 0) or 0),
            int(getattr(gebruik, "output_tokens", 0) or 0),
        )
        self.budget.boek(rol, kosten)

        data: dict[str, Any] = {}
        if schema is not None and tekst and not geweigerd and not afgekapt:
            try:
                data = json.loads(tekst)
            except json.JSONDecodeError:
                # Het antwoord is afgekapt of onleesbaar. Niet gokken: de
                # aanroeper ziet een leeg resultaat en slaat het symbool over.
                data = {}

        return Antwoord(
            rol=rol,
            model=model,
            tekst=tekst,
            data=data,
            tokens_in=kosten.tokens_in,
            tokens_uit=kosten.tokens_uit,
            usd=kosten.usd,
            geweigerd=geweigerd,
            afgekapt=afgekapt,
        )


class NepClient(ClaudeClient):
    """Een client die niets verstuurt. Alleen voor de tests.

    Hij geeft vaste antwoorden terug en rekent verzonnen tokens af, zodat de
    hele keten getest kan worden zonder sleutel, zonder internet en zonder
    kosten.
    """

    def __init__(self, budget: Budget, antwoorden: dict[str, dict] | None = None):
        super().__init__(budget)
        self.antwoorden = antwoorden or {}
        self.aanroepen: list[dict] = []

    def vraag(
        self,
        rol: str,
        model: str,
        systeem: str,
        bericht: str,
        max_tokens: int,
        schema: dict | None = None,
        denken: str = "aan",
        inspanning: str = "low",
    ) -> Antwoord:
        self.budget.controleer()
        self.aanroepen.append(
            {"rol": rol, "model": model, "systeem": systeem, "bericht": bericht}
        )
        data = self.antwoorden.get(rol, {})
        tekst = json.dumps(data) if data else ""
        kosten = bereken_kosten(self.budget.prijzen, model, 1000, 200)
        self.budget.boek(rol, kosten)
        return Antwoord(
            rol=rol,
            model=model,
            tekst=tekst,
            data=dict(data),
            tokens_in=kosten.tokens_in,
            tokens_uit=kosten.tokens_uit,
            usd=kosten.usd,
        )
