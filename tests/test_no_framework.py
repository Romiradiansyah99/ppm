from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED_IMPORTS = (
    "import langchain",
    "from langchain",
    "import langgraph",
    "from langgraph",
    "import deepagents",
    "from deepagents",
)
BANNED_PACKAGES = ("langchain", "langgraph", "deepagents")


def test_no_legacy_agent_imports_or_dependencies():
    targets = [ROOT / "src", ROOT / "app"]
    targets.extend(
        path for path in (ROOT / "tests").glob("*.py")
        if path.name != "test_no_framework.py"
    )
    offenders: list[str] = []
    for target in targets:
        if target.is_dir():
            files = target.rglob("*.py")
        else:
            files = [target]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(pattern in text for pattern in BANNED_IMPORTS):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"legacy harness imports remain: {offenders}"

    for name in ("requirements.in", "requirements.txt"):
        text = (ROOT / name).read_text(encoding="utf-8", errors="ignore").lower()
        assert not any(package in text for package in BANNED_PACKAGES), name
