"""HTTP/SSE client for the private Hermes Agent API server.

Hermes' Chat Completions endpoint is used with a durable Hermes session per
PPM conversation. PPM Postgres remains the user-facing source of truth; the
session is working memory. Built-in Hermes memory is disabled in deployment.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx


class HermesError(RuntimeError):
    """Hermes returned an error or could not be reached."""


class HermesSessionMissing(HermesError):
    """The stored Hermes session no longer exists."""


def _api_root(url: str) -> str:
    base = (url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def _server_root(url: str) -> str:
    api = _api_root(url)
    return api[:-3] if api.endswith("/v1") else api


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in value
        )
    return "" if value is None else str(value)


def _json_content(value: Any) -> dict | None:
    text = _text(value).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _normalise_messages(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("messages", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        session = payload.get("session")
        if isinstance(session, dict):
            return _normalise_messages(session)
    return []


def extract_results(messages: list[dict]) -> dict[str, Any]:
    """Recover artifacts and drafts from the session's full tool transcript."""
    artifacts: list[dict] = []
    documents: list[dict] = []
    tool_calls: list[str] = []
    answer = ""

    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant":
            content = _text(message.get("content")).strip()
            if content:
                answer = content
        if role not in {"tool", "function"}:
            continue
        name = str(message.get("name") or message.get("tool_name") or "tool")
        tool_calls.append(name)
        payload = _json_content(message.get("content"))
        if not payload:
            continue
        if payload.get("run_id") or payload.get("artifacts"):
            artifacts.extend(payload.get("artifacts") or [])
        if payload.get("status") in {"draft", "ambiguous", "not_found", "no_source"}:
            documents.append(payload)
        elif payload.get("generated_id"):
            documents.append(payload)

    return {
        "answer": answer,
        "artifacts": artifacts,
        "documents": documents,
        "tool_calls": tool_calls,
    }


class HermesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "hermes-agent",
        timeout: int = 180,
    ) -> None:
        if not base_url or not api_key:
            raise HermesError("Hermes is not configured")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.api_root = _api_root(base_url)
        self.server_root = _server_root(base_url)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }

    @property
    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=10.0, read=float(self.timeout), write=30.0, pool=10.0)

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
                response = client.get(f"{self.server_root}/health")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def create_session(self, title: str = "PPM conversation") -> str:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self.server_root}/api/sessions",
                headers=self.headers,
                json={"title": title[:200]},
            )
            if response.status_code >= 400:
                raise HermesError(
                    f"could not create Hermes session ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise HermesError("Hermes session response was not JSON") from exc
        session_id = (
            data.get("session_id")
            or data.get("id")
            or (data.get("session") or {}).get("session_id")
            or (data.get("session") or {}).get("id")
        )
        if not session_id:
            raise HermesError("Hermes did not return a session id")
        return str(session_id)

    def delete_session(self, session_id: str) -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
                client.delete(f"{self.server_root}/api/sessions/{session_id}", headers=self.headers)
        except httpx.HTTPError:
            pass

    def fetch_messages(self, session_id: str) -> list[dict]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(
                f"{self.server_root}/api/sessions/{session_id}/messages",
                headers=self.headers,
            )
        if response.status_code == 404:
            raise HermesSessionMissing("Hermes session no longer exists")
        if response.status_code >= 400:
            raise HermesError(
                f"could not read Hermes session ({response.status_code}): {response.text[:300]}"
            )
        try:
            return _normalise_messages(response.json())
        except ValueError:
            return []

    def stream_turn(
        self,
        *,
        session_id: str | None,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        title: str = "PPM conversation",
    ) -> Iterator[dict[str, Any]]:
        """Yield thinking/tool/token/final events.

        If `session_id` is None, a stateless request is made with `history`.
        A 404 with `session_id` raises HermesSessionMissing; the caller may
        create a new session and retry.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history and not session_id:
            messages.extend(
                {"role": item["role"], "content": item["content"]}
                for item in history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            )
        messages.append({"role": "user", "content": user_message})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        headers = dict(self.headers)
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        started = time.monotonic()
        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        usage: dict[str, Any] = {}
        tool_events: list[dict[str, Any]] = []

        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.api_root}/chat/completions",
                    headers=headers,
                    json=body,
                ) as response:
                    if response.status_code == 404 and session_id:
                        raise HermesSessionMissing("Hermes session no longer exists")
                    if response.status_code >= 400:
                        detail = response.read().decode("utf-8", errors="replace")
                        raise HermesError(
                            f"Hermes returned {response.status_code}: {detail[:500]}"
                        )
                    event_name = ""
                    data_lines: list[str] = []
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                            continue
                        if line.strip():
                            continue
                        if not data_lines:
                            event_name = ""
                            continue
                        payload = "\n".join(data_lines)
                        data_lines = []
                        event = event_name
                        event_name = ""
                        if payload == "[DONE]":
                            continue
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        for emitted in self._events_from_payload(
                            data, event, started, answer_parts, thinking_parts, usage, tool_events
                        ):
                            yield emitted
        except HermesSessionMissing:
            raise
        except httpx.HTTPError as exc:
            raise HermesError(f"Hermes request failed: {exc}") from exc

        answer = "".join(answer_parts).strip()
        yield {
            "type": "final",
            "answer": answer,
            "usage": usage,
            "tool_calls": tool_events,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    def _events_from_payload(
        self,
        data: dict,
        event_name: str,
        started: float,
        answer_parts: list[str],
        thinking_parts: list[str],
        usage: dict[str, Any],
        tool_events: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        elapsed = int((time.monotonic() - started) * 1000)
        if event_name in {"hermes.tool.progress", "tool.progress"} or data.get("type") == "hermes.tool.progress":
            name = data.get("tool") or data.get("name") or "tool"
            status = str(data.get("status") or data.get("event") or "progress").lower()
            kind = "tool_result" if status in {"complete", "completed", "done", "finished", "failed", "error"} else "tool_start"
            item = {
                "type": kind,
                "name": name,
                "content": data.get("preview") or data.get("message") or data.get("output"),
                "error": bool(data.get("error")) or status in {"failed", "error"},
                "elapsed_ms": elapsed,
            }
            tool_events.append(item)
            yield item
            return

        choices = data.get("choices") or []
        if choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or choice.get("message") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                thinking_parts.append(str(reasoning))
                yield {"type": "thinking", "message": str(reasoning), "elapsed_ms": elapsed}
            content = delta.get("content")
            if content:
                text = _text(content)
                if text:
                    answer_parts.append(text)
                    yield {"type": "token", "content": text, "elapsed_ms": elapsed}
            if choice.get("finish_reason"):
                usage.update(data.get("usage") or {})
                yield {"type": "llm_end", "usage": usage, "elapsed_ms": elapsed}
            return

        if data.get("type") in {"message.delta", "assistant.delta"}:
            text = _text(data.get("text") or data.get("delta") or data.get("content"))
            if text:
                answer_parts.append(text)
                yield {"type": "token", "content": text, "elapsed_ms": elapsed}
            return
        if data.get("type") in {"reasoning.delta", "thinking.delta"}:
            text = _text(data.get("text") or data.get("delta"))
            if text:
                yield {"type": "thinking", "message": text, "elapsed_ms": elapsed}
            return
        if data.get("usage"):
            usage.update(data["usage"])
            yield {"type": "llm_end", "usage": usage, "elapsed_ms": elapsed}

    def run_turn(
        self,
        *,
        session_id: str | None,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
    ) -> tuple[dict[str, Any], list[dict]]:
        events: list[dict] = []
        final: dict[str, Any] = {}
        for event in self.stream_turn(
            session_id=session_id,
            system_prompt=system_prompt,
            user_message=user_message,
            history=history,
        ):
            events.append(event)
            if event.get("type") == "final":
                final = event
        return final, events
