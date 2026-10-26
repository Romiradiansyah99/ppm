from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ppm.hermes.client import HermesClient, _api_root, extract_results


def test_api_root_normalisation():
    assert _api_root("http://hermes:8642") == "http://hermes:8642/v1"
    assert _api_root("http://hermes:8642/v1") == "http://hermes:8642/v1"
    assert _api_root("http://hermes:8642/v1/chat/completions") == "http://hermes:8642/v1"


def test_extract_results_reads_tool_payloads():
    messages = [
        {"role": "assistant", "content": "Done."},
        {"role": "tool", "name": "plan_and_run", "content": json.dumps({
            "run_id": "r1",
            "artifacts": [{"kind": "chart", "name": "costs", "artifact_id": "a1"}],
        })},
        {"role": "tool", "name": "create_document", "content": json.dumps({
            "status": "draft", "generated_id": "g1", "title": "Draft",
        })},
    ]
    result = extract_results(messages)
    assert result["answer"] == "Done."
    assert result["artifacts"][0]["artifact_id"] == "a1"
    assert result["documents"][0]["generated_id"] == "g1"


def test_stream_turn_parses_sse_and_tool_progress():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "checking"}}]}) + "\n\n",
                "event: hermes.tool.progress\n",
                "data: " + json.dumps({"tool": "sql_query", "status": "started"}) + "\n\n",
                "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}) + "\n\n",
                "data: " + json.dumps({"choices": [{"delta": {"content": " world"}}]}) + "\n\n",
                "data: [DONE]\n\n",
            ]
            for chunk in chunks:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HermesClient(
            f"http://127.0.0.1:{server.server_port}",
            "test-key",
            timeout=10,
        )
        events = list(client.stream_turn(
            session_id=None,
            system_prompt="system",
            user_message="hello",
        ))
    finally:
        server.shutdown()
        server.server_close()

    kinds = [event["type"] for event in events]
    assert "thinking" in kinds
    assert "tool_start" in kinds
    assert "token" in kinds
    assert kinds[-1] == "final"
    assert events[-1]["answer"] == "Hello world"
    assert any(event.get("name") == "sql_query" for event in events)
