"""Manifest-driven corpus discovery.

Every file that enters the pipeline is classified here, not in the ingest graph:
collection, licence class, whether it may be indexed, its template metadata and
its stage. Rules live in corpus/manifest.yaml so they can be audited and changed
without touching code.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ppm.corpus.catalog import infer_category, infer_stage, parse_title
from ppm.corpus.redact import load_blocklist, redact_text

INDEXABLE_SUFFIXES = {".xlsx", ".docx", ".pdf", ".pptx"}
CATALOG_ONLY_SUFFIXES = {".doc"}
SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache", "exports"}


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class CorpusEntry:
    path: Path
    relative_path: str
    collection: str
    licence_class: str
    index: bool
    is_template: bool
    stage: str
    category: str
    code: str | None
    title: str
    format: str
    is_blank: bool
    has_sample_content: bool

    def to_state(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "relative_path": self.relative_path,
            "collection": self.collection,
            "licence_class": self.licence_class,
            "index": self.index,
            "is_template": self.is_template,
            "stage": self.stage,
            "category": self.category,
            "code": self.code,
            "title": self.title,
            "format": self.format,
            "is_blank": self.is_blank,
            "has_sample_content": self.has_sample_content,
        }

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> "CorpusEntry":
        return cls(
            path=Path(data["path"]),
            relative_path=data["relative_path"],
            collection=data["collection"],
            licence_class=data["licence_class"],
            index=bool(data["index"]),
            is_template=bool(data.get("is_template", True)),
            stage=data.get("stage", "unclassified"),
            category=data.get("category", "other"),
            code=data.get("code"),
            title=data["title"],
            format=data.get("format", Path(data["path"]).suffix.lstrip(".").lower()),
            is_blank=bool(data.get("is_blank", True)),
            has_sample_content=bool(data.get("has_sample_content", False)),
        )


@dataclass(frozen=True)
class Exclusion:
    path: str
    reason: str


@dataclass
class CorpusReport:
    base_dir: Path
    entries: list[CorpusEntry] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def indexable(self) -> list[CorpusEntry]:
        return [entry for entry in self.entries if entry.index]

    @property
    def catalog_only(self) -> list[CorpusEntry]:
        return [entry for entry in self.entries if not entry.index and entry.is_template]

    def by_collection(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.collection] = counts.get(entry.collection, 0) + 1
        return dict(sorted(counts.items()))

    def by_licence(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.licence_class] = counts.get(entry.licence_class, 0) + 1
        return dict(sorted(counts.items()))


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise ManifestError(f"no manifest at {manifest_path}")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "roots" not in data:
        raise ManifestError("manifest must contain a 'roots' list")
    return data


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    index = 0
    out = ""
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            out += ".*"
            index += 2
        elif pattern[index] == "*":
            out += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(pattern[index])
            index += 1
    return re.compile("^" + out + "$", re.IGNORECASE)


def glob_match(relative_path: str, pattern: str) -> bool:
    return bool(_glob_to_regex(pattern).match(relative_path.replace("\\", "/")))


def matches_any(relative_path: str, patterns: list[str] | None) -> bool:
    return any(glob_match(relative_path, pattern) for pattern in patterns or [])


def _override_for(relative_path: str, file_name: str, overrides: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for override in overrides:
        pattern = override.get("match")
        if not pattern:
            continue
        if glob_match(relative_path, pattern) or glob_match(file_name, pattern):
            merged.update({key: value for key, value in override.items() if key != "match"})
    return merged


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _detect_sample(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "sample", "best practice", "example", " - kaiyun", "barclays", "chevron",
        "julius bar", " mbs", "abbeyfield", "citi ", "dc2b", "nets ", "cgk11", "gdc_",
    ]
    return any(marker in lowered for marker in markers)


def scan_file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def resolve_corpus(manifest_path: str | Path) -> CorpusReport:
    manifest = load_manifest(manifest_path)
    base_dir = Path(manifest.get("base_dir", ".")).expanduser().resolve()
    blocklist = load_blocklist(Path(manifest_path).parent / "client_blocklist.txt")
    global_exclude = manifest.get("exclude") or []
    overrides = manifest.get("overrides") or []
    skip_paths = [(base_dir / rel).resolve() for rel in manifest.get("skip_paths") or []]

    report = CorpusReport(base_dir=base_dir)
    seen: set[Path] = set()

    for root in manifest["roots"]:
        root_path = (base_dir / root["path"]).expanduser().resolve()
        if not root_path.is_dir():
            report.warnings.append(f"missing root: {root_path}")
            continue
        collection = root["collection"]
        default_licence = root.get("licence", "unknown")
        root_exclude = root.get("exclude") or []

        for file_path in sorted(root_path.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in file_path.parts):
                continue

            absolute = file_path.resolve()
            if absolute in seen:
                report.excluded.append(Exclusion(str(absolute), "duplicate_path"))
                continue

            relative_base = absolute.relative_to(base_dir).as_posix() if _is_under(absolute, base_dir) else absolute.name
            relative_root = absolute.relative_to(root_path).as_posix()

            if any(_is_under(absolute, skipped) for skipped in skip_paths):
                report.excluded.append(Exclusion(str(absolute), "duplicate_root"))
                continue
            if matches_any(relative_base, global_exclude) or matches_any(relative_root, root_exclude):
                report.excluded.append(Exclusion(str(absolute), "excluded_pattern"))
                continue

            suffix = file_path.suffix.lower()
            if suffix in CATALOG_ONLY_SUFFIXES:
                file_format = suffix.lstrip(".")
            elif suffix in INDEXABLE_SUFFIXES:
                file_format = suffix.lstrip(".")
            else:
                report.excluded.append(Exclusion(str(absolute), f"unsupported_format:{suffix or 'none'}"))
                continue

            override = _override_for(relative_base, file_path.name, overrides)
            effective_collection = override.get("collection", collection)
            licence = override.get("licence", default_licence)

            default_index = licence in {"office_authored", "permitted"} and suffix in INDEXABLE_SUFFIXES
            index_allowed = bool(override.get("index", default_index))

            stem_title = parse_title(file_path.name)
            clean_title, _ = redact_text(stem_title, blocklist)
            is_template = bool(override.get("is_template", True))
            has_sample = _detect_sample(f"{relative_base} {stem_title}")

            # Only accepted files claim a path; an excluded file must remain
            # available to a later root (ttpm_flat excludes 03 PM Templates 2020,
            # which is its own root).
            seen.add(absolute)
            report.entries.append(CorpusEntry(
                path=absolute,
                relative_path=relative_base,
                collection=effective_collection,
                licence_class=licence,
                index=index_allowed,
                is_template=is_template,
                stage=override.get("stage") or infer_stage(relative_root, stem_title),
                category=override.get("category") or infer_category(stem_title),
                code=override.get("code") or _parse_code(file_path.name),
                title=clean_title,
                format=file_format,
                is_blank=not has_sample,
                has_sample_content=has_sample,
            ))

    report.entries.sort(key=lambda entry: (entry.collection, entry.code or "zzz", entry.title.lower()))
    return report


_CODE_RE = re.compile(r"^(?P<code>\d{1,3}[a-z]?)\s*-\s*(?P<title>.+)$", re.IGNORECASE)


def _parse_code(file_name: str) -> str | None:
    stem = Path(file_name).stem
    match = _CODE_RE.match(stem)
    return match.group("code") if match else None
