"""PPM — public, multi-user Hermes Agent chat."""

from __future__ import annotations

import html
import json
import os
import sys
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(REPO_ROOT / "src"))

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st

import theme
from ppm import db
from ppm.analyst.documents import generated_document_bytes, get_generated_document
from ppm.analyst.memory import (
    clear as clear_memory,
    count as memory_count,
    delete_memory,
    delete_preference,
    extract_memories,
    list_memories,
    list_preferences,
    memory_context,
    set_preference,
    should_extract,
)
from ppm.auth import (
    accept_invite,
    audit,
    ensure_seed_users,
    login,
    resolve_session,
    revoke_session,
)
from ppm.config import ConfigError, get_settings
from ppm.conversations import (
    add_message,
    create_conversation,
    get_conversation,
    get_last_conversation,
    list_conversations,
    load_messages,
    set_hermes_session,
)
from ppm.hermes import HermesClient, HermesError, HermesSessionMissing, build_system_prompt, extract_results
from ppm.tool_context import issue as issue_context

st.set_page_config(page_title="PPM", layout="centered", initial_sidebar_state="expanded")
theme.inject()

try:
    settings = get_settings()
except ConfigError as exc:
    st.error(f"PPM is not configured: {exc}")
    st.stop()

try:
    ensure_seed_users()
except Exception as exc:
    st.error(f"Database is not ready: {exc}. Run `ppm init-db` first.")
    st.stop()


def _cookie_manager():
    return stx.CookieManager(key="ppm_cookies")


def _set_cookie(manager, token: str) -> None:
    try:
        manager.set(
            "ppm_session",
            token,
            expires_at=time.time() + settings.session_days * 86400,
            secure=settings.cookie_secure,
            same_site="lax",
        )
    except Exception:
        # AppTest and non-browser clients may not mount the component.
        pass


def _delete_cookie(manager) -> None:
    try:
        manager.delete("ppm_session")
    except Exception:
        pass


def _current_user(manager) -> dict | None:
    if st.session_state.get("user"):
        return st.session_state["user"]
    token = None
    try:
        token = manager.get("ppm_session")
    except Exception:
        token = None
    if token:
        user = resolve_session(token)
        if user:
            st.session_state["user"] = user
            return user
    return None


def _login_view(manager) -> None:
    st.markdown('<div class="login-title">Sign in</div>', unsafe_allow_html=True)
    mode = st.radio(
        "Access",
        ["Sign in", "Redeem invite"],
        horizontal=True,
        label_visibility="collapsed",
        key="login_mode",
    )
    if mode == "Sign in":
        left, center, right = st.columns([1, 1.1, 1])
        with center:
            with st.form("login"):
                username = st.text_input("Username", placeholder="Username", label_visibility="collapsed")
                password = st.text_input(
                    "Password", type="password", placeholder="Password", label_visibility="collapsed"
                )
                submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
            if submitted:
                user, token, error = login(
                    username,
                    password,
                    days=settings.session_days,
                )
                if not user or not token:
                    st.error(
                        "Too many attempts. Try again later."
                        if error == "locked"
                        else "Incorrect username or password."
                    )
                else:
                    st.session_state["user"] = user
                    st.session_state.pop("thread_id", None)
                    _set_cookie(manager, token)
                    st.rerun()
            if not settings.public_mode:
                st.markdown(
                    '<div class="login-hint">senior / senior123 · junior / junior123</div>',
                    unsafe_allow_html=True,
                )
    else:
        left, center, right = st.columns([1, 1.1, 1])
        with center:
            with st.form("redeem-invite"):
                code = st.text_input("Invite code", type="password", placeholder="Invite code")
                username = st.text_input("Choose a username", placeholder="Username")
                display_name = st.text_input("Display name", placeholder="Your name")
                password = st.text_input("Choose a password", type="password", placeholder="Password")
                submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
            if submitted:
                try:
                    user = accept_invite(code, username, display_name, password)
                    token = login(username, password, days=settings.session_days)[1]
                    if not token:
                        raise ValueError("account created; please sign in")
                    st.session_state["user"] = user
                    _set_cookie(manager, token)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def _user_bubble(content: str) -> None:
    safe = html.escape(content).replace("\n", "<br>")
    st.markdown(
        f'<div class="msg-row user"><div class="bubble">{safe}</div></div>',
        unsafe_allow_html=True,
    )


def _todos_markdown(value) -> str:
    if isinstance(value, dict):
        value = value.get("todos") or []
    if not isinstance(value, list):
        return ""
    marks = {"completed": "✓", "in_progress": "→", "pending": "○"}
    lines = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("content") or item.get("task") or item.get("description") or str(item)
            status = str(item.get("status") or "pending")
        else:
            text, status = str(item), "pending"
        lines.append(f"- {marks.get(status, '○')} {text}")
    return "\n".join(lines)


def _activity(events: list[dict]) -> None:
    useful = [
        event for event in events
        if event.get("type") in {"thinking", "tool_start", "tool_result", "llm_end", "error"}
    ]
    if not useful:
        return
    with st.expander("Activity"):
        for event in useful:
            kind = event.get("type")
            suffix = f" · {event.get('elapsed_ms')} ms" if event.get("elapsed_ms") is not None else ""
            if kind == "thinking":
                st.caption(f"Thinking{suffix}")
            elif kind == "tool_start":
                st.markdown(f"**→ `{event.get('name')}`**{suffix}")
            elif kind == "tool_result":
                mark = "!" if event.get("error") else "✓"
                st.markdown(f"**{mark} `{event.get('name')}`**{suffix}")
            elif kind == "llm_end":
                usage = event.get("usage") or {}
                tokens = usage.get("total_tokens")
                st.caption(f"Model finished{f' · {tokens} tokens' if tokens else ''}{suffix}")
            elif kind == "error":
                st.error(event.get("error") or "Agent error")


def _result_events(events: list[dict]) -> dict:
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return {}


def _artifact(artifact: dict, user: dict, conversation_id: str) -> None:
    artifact_id = artifact.get("artifact_id")
    if not artifact_id:
        return
    row = db.fetch_one(
        "SELECT a.* FROM analysis_artifact a JOIN analysis_run r ON r.run_id = a.run_id "
        "WHERE a.artifact_id = %s AND r.user_id = %s AND r.conversation_id = %s",
        (artifact_id, user["user_id"], conversation_id),
    )
    if not row:
        return
    path = Path(str(row.get("path") or ""))
    if not path.is_file():
        return
    kind = row.get("kind")
    if kind == "dataframe":
        try:
            st.dataframe(pd.read_parquet(path), width="stretch", hide_index=True)
        except Exception:
            st.caption(f"Could not read dataframe: {path.name}")
    elif kind == "chart":
        st.image(str(path), width="stretch")
    elif kind == "report":
        with st.expander("Report"):
            st.markdown(path.read_text(encoding="utf-8"))


def _document(document: dict, user: dict) -> None:
    if document.get("status") == "ambiguous":
        names = ", ".join(
            f"{item.get('code') or '-'} {item.get('title')}"
            for item in document.get("candidates") or []
        )
        st.info(f"Several templates match: {names}. Please provide the code.")
        return
    generated_id = document.get("generated_id")
    if not generated_id:
        return
    row = get_generated_document(user["user_id"], generated_id)
    if not row:
        return
    st.markdown(f"**Draft document** · {row['title']}")
    payload = generated_document_bytes(user["user_id"], generated_id)
    if payload:
        data, filename, mime = payload
        st.download_button(
            "Download draft",
            data=data,
            file_name=filename,
            mime=mime,
            key=f"download-{generated_id}",
        )
    unfilled = document.get("unfilled") or []
    if unfilled:
        st.caption("Not filled: " + ", ".join(unfilled))
    st.caption("Draft status. Human review is required before client use.")


def _message(message: dict, user: dict, conversation_id: str) -> None:
    content = message.get("content") or ""
    if message.get("role") == "user":
        _user_bubble(content)
        return
    with st.container(key=f"assistant-{message.get('message_id')}"):
        st.markdown(content)
        events = message.get("events") or []
        result = _result_events(events)
        for artifact in result.get("artifacts") or []:
            _artifact(artifact, user, conversation_id)
        for document in result.get("documents") or []:
            _document(document, user)
        _activity(events)


def _memory_panel(user: dict) -> None:
    with st.expander(f"Memory & preferences ({memory_count(user['user_id'])})"):
        st.caption("These are yours alone. Hermes built-in memory is disabled.")
        preferences = list_preferences(user["user_id"])
        if preferences:
            for pref in preferences:
                cols = st.columns([5, 1])
                cols[0].markdown(f"**{pref['pref_key']}** · {pref['pref_value']}")
                if cols[1].button("Remove", key=f"del-pref-{pref['pref_key']}"):
                    delete_preference(user["user_id"], pref["pref_key"])
                    st.rerun()
        with st.form("add-preference", clear_on_submit=True):
            key = st.text_input("Preference name", placeholder="e.g. answer_style")
            value = st.text_input("Preference value", placeholder="e.g. concise, with a table")
            if st.form_submit_button("Save preference"):
                try:
                    set_preference(user["user_id"], key, value)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        memories = list_memories(user["user_id"], limit=8)
        for memory in memories:
            cols = st.columns([5, 1])
            cols[0].caption(f"[{memory['kind']}] {memory['content']}")
            if cols[1].button("Remove", key=f"del-memory-{memory['memory_id']}"):
                delete_memory(user["user_id"], str(memory["memory_id"]))
                st.rerun()
        if memories and st.button("Clear all memory", key="clear-memory"):
            clear_memory(user["user_id"])
            st.rerun()


def _sidebar(user: dict, conversations: list[dict], active_thread: str, manager) -> str:
    with st.sidebar:
        if st.button("New chat", use_container_width=True):
            conversation = create_conversation(user["user_id"])
            st.session_state["thread_id"] = conversation["thread_id"]
            st.rerun()
        st.markdown('<div class="side-label">Threads</div>', unsafe_allow_html=True)
        for item in conversations[:20]:
            label = item["title"] or "New conversation"
            if len(label) > 42:
                label = label[:42] + "…"
            if st.button(
                label,
                key=f"thread-{item['thread_id']}",
                use_container_width=True,
                type="primary" if item["thread_id"] == active_thread else "secondary",
            ):
                st.session_state["thread_id"] = item["thread_id"]
                st.rerun()
        _memory_panel(user)
        st.markdown('<div class="side-label">Account</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="side-user">{html.escape(user["display_name"])}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="side-meta">{user["role"].replace("_", " ")} · {user["access_level"]}</div>',
            unsafe_allow_html=True,
        )
        st.caption("Hermes Agent · private" if settings.hermes_analyst_url else "Hermes not configured")
        if st.button("Log out", use_container_width=True):
            token = None
            try:
                token = manager.get("ppm_session")
            except Exception:
                token = None
            revoke_session(token)
            _delete_cookie(manager)
            for key in ("user", "thread_id", "pending_prompt", "view"):
                st.session_state.pop(key, None)
            st.rerun()
        if user["role"] == "senior_manager":
            view = st.radio("View", ["Chat", "Admin"], key="view", horizontal=True)
        else:
            view = "Chat"
    return view


def _hermes_client(user: dict) -> HermesClient:
    base, key = settings.hermes_for(user["role"])
    return HermesClient(
        base,
        key,
        model=settings.hermes_model,
        timeout=settings.hermes_timeout,
    )


def _run_turn(user: dict, conversation: dict, prompt: str, status, live) -> tuple[str, list[dict]]:
    client = _hermes_client(user)
    session_id = conversation.get("hermes_session_id")
    if not session_id:
        session_id = client.create_session(title=conversation["title"])
        set_hermes_session(conversation["conversation_id"], session_id)
        conversation["hermes_session_id"] = session_id

    token = issue_context(user, conversation["conversation_id"], settings.tool_secret)
    memory = memory_context(user["user_id"], prompt)
    system_prompt = build_system_prompt(
        user=user,
        memory=memory,
        summary=conversation.get("summary"),
        context_token=token,
    )
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in load_messages(conversation["conversation_id"], limit=20)
        if item["role"] in {"user", "assistant"}
    ][:-1]

    events: list[dict] = []
    answer = ""
    result: dict = {}

    def consume(sid: str | None, replay: list[dict] | None):
        nonlocal answer, result
        for event in client.stream_turn(
            session_id=sid,
            system_prompt=system_prompt,
            user_message=prompt,
            history=replay,
        ):
            events.append(event)
            kind = event.get("type")
            if kind == "thinking":
                status.update(label="Thinking...")
            elif kind == "tool_start":
                status.update(label=f"Calling {event.get('name')}...")
            elif kind == "token":
                answer += event.get("content") or ""
                live.markdown(answer + "▌")
            elif kind == "tool_result":
                status.update(label="Composing answer...")
            elif kind == "final":
                answer = event.get("answer") or answer
                result = event
                status.update(label="Done", state="complete", expanded=False)

    try:
        consume(session_id, None)
    except HermesSessionMissing:
        session_id = client.create_session(title=conversation["title"])
        set_hermes_session(conversation["conversation_id"], session_id)
        consume(None, history)

    try:
        transcript = client.fetch_messages(session_id)
        extracted = extract_results(transcript)
        if extracted.get("artifacts"):
            result["artifacts"] = extracted["artifacts"]
        if extracted.get("documents"):
            result["documents"] = extracted["documents"]
        if not answer and extracted.get("answer"):
            answer = extracted["answer"]
    except HermesError:
        pass

    live.empty()
    result.setdefault("artifacts", [])
    result.setdefault("documents", [])
    events.append({"type": "result", "artifacts": result["artifacts"], "documents": result["documents"]})
    return answer or "I did not return an answer.", events


def _chat(user: dict, manager) -> None:
    conversations = list_conversations(user["user_id"])
    thread_id = st.session_state.get("thread_id")
    conversation = get_conversation(user["user_id"], thread_id=thread_id) if thread_id else None
    if not conversation:
        conversation = get_last_conversation(user["user_id"]) or create_conversation(user["user_id"])
        st.session_state["thread_id"] = conversation["thread_id"]

    view = _sidebar(user, conversations, conversation["thread_id"], manager)
    if view == "Admin":
        _admin(user)
        return

    messages = load_messages(conversation["conversation_id"], limit=100)
    if not messages:
        st.markdown('<div class="empty-title">How can I help?</div>', unsafe_allow_html=True)
        examples = [
            "What is the largest cost component in the benchmark projects?",
            "Which PEP template fits, and can you draft it?",
            "Which risk has the highest score?",
        ]
        columns = st.columns(3)
        for index, (column, example) in enumerate(zip(columns, examples)):
            if column.button(example, key=f"example-{index}"):
                st.session_state["pending_prompt"] = example
                st.rerun()

    for message in messages:
        _message(message, user, conversation["conversation_id"])

    prompt = st.session_state.pop("pending_prompt", None)
    if prompt is None:
        prompt = st.chat_input("Ask anything...")
    if not prompt:
        return
    prompt = prompt.strip()
    if len(prompt) > settings.max_input_chars:
        st.error(f"Please keep the question under {settings.max_input_chars} characters.")
        return
    from ppm.conversations import count_user_messages_today

    if count_user_messages_today(user["user_id"]) >= settings.daily_message_limit:
        st.error("You have reached today's message limit. Please try again tomorrow.")
        return

    add_message(conversation["conversation_id"], "user", prompt)
    with st.status("Thinking...", expanded=True) as status:
        live = st.empty()
        try:
            answer, events = _run_turn(user, conversation, prompt, status, live)
        except Exception as exc:
            answer = f"I could not complete that request: {exc}"
            events = [{"type": "error", "error": str(exc)}]
            status.update(label="Failed", state="error", expanded=False)
            st.error(str(exc))
    add_message(conversation["conversation_id"], "assistant", answer, events=events)
    audit(user["user_id"], "chat.turn", target=conversation["thread_id"], detail={"chars": len(prompt)})
    if should_extract(prompt):
        try:
            extract_memories(
                user,
                f"User: {prompt}\nAssistant: {answer}",
                settings,
                conversation_id=conversation["conversation_id"],
            )
        except Exception:
            pass
    st.rerun()


def _admin(user: dict) -> None:
    st.markdown("### Admin")
    st.caption("Senior-only operations. The public chat has no write tools.")
    tabs = st.tabs(["Ingest", "Review queue", "Data & corpus", "Validation"])

    with tabs[0]:
        uploads = st.file_uploader(
            "Upload files to quarantine",
            type=["docx", "xlsx", "pptx", "pdf"],
            accept_multiple_files=True,
            key="admin-uploads",
        )
        default_template = st.checkbox("Treat uploads as templates", value=False)
        if uploads and st.button("Ingest uploaded files", type="primary"):
            from ppm.corpus.manifest import CorpusEntry
            from ppm.workflows.ingest import IngestWorkflow

            settings.incoming_dir.mkdir(parents=True, exist_ok=True)
            workflow = IngestWorkflow(settings)
            for upload in uploads:
                safe = Path(upload.name).name
                target = settings.incoming_dir / f"{uuid.uuid4().hex[:8]}-{safe}"
                target.write_bytes(upload.getbuffer())
                entry = CorpusEntry(
                    path=target,
                    relative_path=safe,
                    collection="uploaded",
                    licence_class="office_authored",
                    index=True,
                    is_template=bool(default_template),
                    stage="unclassified",
                    category="other",
                    code=None,
                    title=Path(safe).stem,
                    format=target.suffix.lower().lstrip("."),
                    is_blank=True,
                    has_sample_content=False,
                )
                with st.spinner(f"Ingesting {safe}..."):
                    result = workflow.start(entry)
                st.write(f"**{safe}**: {result.get('status')} {result.get('error') or ''}")
                if result.get("thread_id"):
                    st.caption(f"thread: {result['thread_id']}")
            audit(user["user_id"], "admin.ingest", target="uploaded")

    with tabs[1]:
        rows = db.fetch_all(
            "SELECT thread_id, source_path, stats FROM ingest_run "
            "WHERE status = 'awaiting_review' ORDER BY created_at"
        )
        if not rows:
            st.info("The review queue is empty.")
        for row in rows:
            payload = (row.get("stats") or {}).get("review") or {}
            st.markdown(f"**{Path(str(row['source_path'])).name}**")
            st.caption(", ".join(payload.get("reasons") or []) or "review required")
            cols = st.columns(2)
            if cols[0].button("Approve redacted", key=f"approve-{row['thread_id']}"):
                from ppm.workflows.ingest import IngestWorkflow

                IngestWorkflow(settings).resume(row["thread_id"], {"action": "approve_redacted"})
                audit(user["user_id"], "admin.review.approve", target=row["thread_id"])
                st.rerun()
            if cols[1].button("Reference only", key=f"reject-{row['thread_id']}"):
                from ppm.workflows.ingest import IngestWorkflow

                IngestWorkflow(settings).resume(row["thread_id"], {"action": "reference_only"})
                audit(user["user_id"], "admin.review.reference", target=row["thread_id"])
                st.rerun()

    with tabs[2]:
        if st.button("Seed structured data"):
            from dataclasses import asdict

            from ppm.data.seed import seed_defaults

            with st.spinner("Seeding..."):
                result = seed_defaults()
            st.json(asdict(result))
        if st.button("Reindex indexed documents"):
            from argparse import Namespace

            from ppm.cli import cmd_reindex

            with st.spinner("Reindexing..."):
                code = cmd_reindex(Namespace(force=False))
            st.write(f"exit code: {code}")
        counts = db.fetch_one(
            "SELECT count(*) AS documents FROM document "
            "WHERE ingest_status IN ('indexed', 'indexed_text_only')"
        )
        st.caption(f"Indexed documents: {counts['documents'] if counts else 0}")

    with tabs[3]:
        if st.button("Validate app specs"):
            from ppm.config import get_settings as _get_settings
            from ppm.registry import load_taxonomy, validate_app_specs, validate_taxonomy

            conf = _get_settings()
            taxonomy = load_taxonomy(conf.taxonomy_path)
            problems = validate_taxonomy(taxonomy, strict=False)
            problems.extend(validate_app_specs(conf.app_specs_dir, taxonomy, strict=False))
            if problems:
                for problem in problems:
                    st.warning(problem)
            else:
                st.success("App specs valid.")


if "user" not in st.session_state:
    manager = _cookie_manager()
    user = _current_user(manager)
else:
    manager = _cookie_manager()
    user = st.session_state["user"]

if not user:
    _login_view(manager)
else:
    _chat(user, manager)
