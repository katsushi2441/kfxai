from __future__ import annotations

import pytest

from kfxai.config import LIVE_ACK, Settings


def test_default_is_paper_and_practice() -> None:
    settings = Settings()
    settings.validate()
    assert settings.trading_mode == "paper"
    assert settings.api_base_url == "https://api-fxpractice.oanda.com"


def test_live_requires_environment_and_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(oanda_environment="live", trading_mode="live")
    with pytest.raises(ValueError, match="KFXAI_LIVE_ACK"):
        settings.validate()
    monkeypatch.setenv("KFXAI_LIVE_ACK", LIVE_ACK)
    settings.validate()


def test_practice_cannot_point_to_live() -> None:
    with pytest.raises(ValueError, match="practice trading requires"):
        Settings(oanda_environment="live", trading_mode="practice").validate()
