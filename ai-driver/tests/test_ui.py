from pathlib import Path

from streamlit.testing.v1 import AppTest

from squash_ai_driver.ui import _parse_event_ids


def test_ui_renders_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SQUASH_API_KEY", raising=False)
    app = Path(__file__).parents[1] / "streamlit_app.py"

    at = AppTest.from_file(app, default_timeout=10).run()

    assert not at.exception
    assert at.title[0].value == "AI endpoint diagnostics"
    assert at.error
    assert "control-plane API key" in at.error[0].value


def test_event_id_parser_bounds_and_deduplicates() -> None:
    assert _parse_event_ids("41, 6008, 41") == [41, 6008]
