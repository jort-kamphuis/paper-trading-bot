"""Je /akkoord uit Telegram lezen: zonder teller, eerst verwerken en dan pas afvinken.

Aanleiding (november 2026): in de proefweek kwam /akkoord 1 nooit aan. De bot vroeg
Telegram altijd om berichten 'vanaf mijn opgeslagen teller'. Volgens de documentatie
van Telegram krijgt het eerste bericht na een week stilte een willekeurig nummer.
Is dat lager dan de teller, dan vinkt Telegram het af zonder het te laten zien.

De nep-Telegram hieronder doet precies wat de documentatie beschrijft:
* zonder 'offset': alles teruggeven wat nog niet is afgevinkt, niets weggooien;
* met 'offset': alles met een lager nummer is daarmee afgevinkt en komt nooit terug.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from bot import config as conf
from bot import database, telegram
from tests.test_stap5 import _klaar_voor_akkoord

PROJECT = Path(__file__).resolve().parent.parent
CHAT = "42"
OUDE_TELLER = 544789342   # de teller die in de proefweek dagenlang bleef staan
ECHT = conf.laad()


class NepTelegram:
    def __init__(self, updates: list[dict], afvinken_mislukt: bool = False):
        self.wachtrij = list(updates)
        self.afvinken_mislukt = afvinken_mislukt
        self.aanroepen: list[dict] = []

    def __call__(self, token, methode, gegevens=None, timeout=20):
        assert methode == "getUpdates"
        gegevens = dict(gegevens or {})
        self.aanroepen.append(gegevens)
        if "offset" in gegevens:
            if self.afvinken_mislukt:
                raise telegram.TelegramFout("ConnectionError: geen verbinding")
            self.wachtrij = [u for u in self.wachtrij if u["update_id"] >= gegevens["offset"]]
        return {"ok": True, "result": self.wachtrij[: gegevens.get("limit", 100)]}


def _bericht(nummer: int, tekst: str, chat: str = CHAT, soort: str = "message") -> dict:
    return {"update_id": nummer, soort: {"chat": {"id": int(chat)}, "text": tekst, "date": 1795000000}}


@pytest.fixture
def cfg():
    return json.loads(json.dumps(ECHT))


@pytest.fixture
def dagelijks(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("script_dagelijks_telegram", PROJECT / "scripts" / "dagelijks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "nep-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setattr(module, "WIJZIGING_BESTAND", tmp_path / ".wijziging.txt")
    return module


def _status(con) -> str:
    return con.execute("SELECT status FROM challengers").fetchone()["status"]


# ------------------------------------------------------------------ het lezen zelf


def test_lezen_gebeurt_zonder_teller_en_gooit_niets_weg(monkeypatch):
    nep = NepTelegram([_bericht(17, "/akkoord 1")])
    monkeypatch.setattr(telegram, "_roep_aan", nep)
    berichten, volgende, ontvangen = telegram.lees_berichten("t", CHAT)
    assert "offset" not in nep.aanroepen[0], "een teller meesturen laat Telegram berichten ongezien afvinken"
    assert [(b["nummer"], b["tekst"]) for b in berichten] == [(17, "/akkoord 1")]
    assert (volgende, ontvangen) == (18, 1)
    assert len(nep.wachtrij) == 1, "lezen alleen vinkt niets af"


def test_zo_ging_het_mis_met_een_opgeslagen_teller():
    """De oude aanpak, nagespeeld op de nep-Telegram: het bericht verdwijnt ongezien."""
    nep = NepTelegram([_bericht(17, "/akkoord 1")])
    assert nep("t", "getUpdates", {"offset": OUDE_TELLER, "timeout": 0})["result"] == []
    assert nep.wachtrij == []


def test_zonder_berichten_valt_er_niets_af_te_vinken(monkeypatch):
    monkeypatch.setattr(telegram, "_roep_aan", NepTelegram([]))
    assert telegram.lees_berichten("t", CHAT) == ([], None, 0)


def test_een_bericht_in_een_kanaal_telt_ook_maar_alleen_uit_je_eigen_chat(monkeypatch):
    nep = NepTelegram([
        _bericht(5, "/akkoord 1", soort="channel_post"),
        _bericht(6, "/akkoord 1", chat="999", soort="channel_post"),
        _bericht(7, "/akkoord 2", chat="999"),
    ])
    monkeypatch.setattr(telegram, "_roep_aan", nep)
    berichten, volgende, ontvangen = telegram.lees_berichten("t", CHAT)
    assert set(nep.aanroepen[0]["allowed_updates"]) == {"message", "channel_post"}
    assert [b["nummer"] for b in berichten] == [5]
    assert (volgende, ontvangen) == (8, 3), "ook genegeerde berichten worden straks afgevinkt"


# ------------------------------------------------- de hele weg, zoals in de nachtronde


def test_een_akkoord_met_een_willekeurig_lager_nummer_komt_gewoon_aan(con, cfg, dagelijks, monkeypatch, capsys):
    """Het geval uit de proefweek: een oude teller in de database, en na een week stilte een laag nummer."""
    c = _klaar_voor_akkoord(con, cfg)
    database.zet_instelling(con, "telegram_offset", str(OUDE_TELLER))   # staat nog in een oude database
    nep = NepTelegram([_bericht(17, f"/akkoord {c['id']}")])
    monkeypatch.setattr(telegram, "_roep_aan", nep)

    antwoorden = dagelijks._verwerk_antwoorden(con, "2026-12-30")

    assert len(antwoorden) == 1 and antwoorden[0].startswith("Akkoord verwerkt")
    assert _status(con) == "aangenomen"
    assert dagelijks.WIJZIGING_BESTAND.read_text(encoding="utf-8").startswith("Akkoord verwerkt")
    assert nep.wachtrij == [], "na het verwerken is het bericht afgevinkt"
    assert [a.get("offset") for a in nep.aanroepen] == [None, 18], "eerst lezen zonder teller, dan pas afvinken"
    assert database.lees_instelling(con, dagelijks.SLEUTEL_TELEGRAM_GEZIEN) == "[]"
    uit = capsys.readouterr().out
    assert "Telegram: 1 bericht(en) opgehaald, 1 uit jouw chat, 0 al eerder verwerkt, 1 opdracht(en)." in uit
    assert CHAT not in uit.replace("2026", ""), "het chatnummer hoort niet in het openbare logboek"


def test_mislukt_afvinken_voert_een_akkoord_niet_twee_keer_uit(con, cfg, dagelijks, monkeypatch):
    c = _klaar_voor_akkoord(con, cfg)
    nep = NepTelegram([_bericht(17, f"/akkoord {c['id']}")], afvinken_mislukt=True)
    monkeypatch.setattr(telegram, "_roep_aan", nep)

    eerste = dagelijks._verwerk_antwoorden(con, "2026-12-30")
    assert len(eerste) == 1 and _status(con) == "aangenomen"
    assert database.lees_instelling(con, dagelijks.SLEUTEL_TELEGRAM_GEZIEN) == "[17]"
    assert len(nep.wachtrij) == 1, "Telegram heeft het bericht nog"

    # De volgende ronde: hetzelfde bericht komt terug, plus een nieuw. Alleen het nieuwe telt.
    nep.afvinken_mislukt = False
    nep.wachtrij.append(_bericht(18, "/afwijzen 99"))
    tweede = dagelijks._verwerk_antwoorden(con, "2026-12-31")
    assert len(tweede) == 1 and "99" in tweede[0], "het oude akkoord wordt niet opnieuw beantwoord"
    assert con.execute("SELECT COUNT(*) n FROM wijzigingen").fetchone()["n"] == 1
    assert nep.wachtrij == []
    assert database.lees_instelling(con, dagelijks.SLEUTEL_TELEGRAM_GEZIEN) == "[]"


def test_gewone_berichten_en_andere_chats_doen_niets_maar_worden_wel_afgevinkt(con, cfg, dagelijks, monkeypatch):
    c = _klaar_voor_akkoord(con, cfg)
    nep = NepTelegram([_bericht(3, "hoi"), _bericht(4, f"/akkoord {c['id']}", chat="999")])
    monkeypatch.setattr(telegram, "_roep_aan", nep)
    assert dagelijks._verwerk_antwoorden(con, "2026-12-30") == []
    assert _status(con) == "wacht_op_akkoord"
    assert nep.wachtrij == []
    assert not dagelijks.WIJZIGING_BESTAND.exists()


def test_valt_telegram_weg_dan_gaat_de_ronde_gewoon_door(con, cfg, dagelijks, monkeypatch):
    def kapot(token, methode, gegevens=None, timeout=20):
        raise telegram.TelegramFout("ConnectionError: geen verbinding")

    _klaar_voor_akkoord(con, cfg)
    monkeypatch.setattr(telegram, "_roep_aan", kapot)
    assert dagelijks._verwerk_antwoorden(con, "2026-12-30") == []
    assert _status(con) == "wacht_op_akkoord"
