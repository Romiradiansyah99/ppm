from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[1]


def test_login_page_and_senior_login(clean_db):
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.text_input) == 2

    at.text_input[0].set_value("senior")
    at.text_input[1].set_value("senior123")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["user"]["username"] == "senior"
    assert at.session_state["user"]["access_level"] == "all"


def test_login_rejects_wrong_password(clean_db):
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=120)
    at.run()
    at.text_input[0].set_value("junior")
    at.text_input[1].set_value("wrong")
    at.button[0].click().run()
    assert "user" not in at.session_state
