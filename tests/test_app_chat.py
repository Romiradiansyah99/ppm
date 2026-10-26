from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ppm import db
from ppm.auth import authenticate
from ppm.config import get_settings
from ppm.conversations import get_last_conversation, load_messages

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]


def test_chat_turn_is_saved_to_the_user_thread(clean_db, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def _json(self, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/messages"):
                self._json({"messages": [{"role": "assistant", "content": "Hello from Hermes."}]})
            else:
                self._json({"status": "ok"})

        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            if self.path.endswith("/api/sessions"):
                self._json({"session_id": "hs_test"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":"Hello from Hermes."}}]}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("PPM_HERMES_ANALYST_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("PPM_HERMES_ANALYST_KEY", "test-key")
        monkeypatch.setenv("PPM_HERMES_ADMIN_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("PPM_HERMES_ADMIN_KEY", "test-key")
        monkeypatch.setenv("PPM_TOOL_SECRET", "test-tool-secret")
        monkeypatch.setenv("PPM_PUBLIC_MODE", "0")
        get_settings.cache_clear()

        at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=120)
        at.run()
        at.text_input[0].set_value("senior")
        at.text_input[1].set_value("senior123")
        at.button[0].click().run()
        assert "user" in at.session_state

        at.chat_input[0].set_value("Hello").run()
        assert not at.exception

        senior = authenticate("senior", "senior123")
        conversation = get_last_conversation(senior["user_id"])
        assert conversation
        messages = load_messages(conversation["conversation_id"])
        assert messages[-1]["role"] == "assistant"
        assert "Hello from Hermes." in messages[-1]["content"]
        assert db.fetch_one(
            "SELECT count(*) AS n FROM conversation WHERE user_id = %s",
            (senior["user_id"],),
        )["n"] >= 1
    finally:
        server.shutdown()
        server.server_close()
        get_settings.cache_clear()
