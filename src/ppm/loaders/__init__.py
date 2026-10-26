"""Custom document loaders: deterministic parsing, plain rows, no LLM.

ParsedDocument keeps tables and text blocks with section references so every
chunk can cite its exact source location.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ppm.corpus.redact import Hit, merge_hits, redact_text

MIME_BY_SUFFIX = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
}

INDEXABLE_SUFFIXES = {".xlsx", ".xlsm", ".docx", ".pptx", ".pdf"}


class UnsupportedFormatError(Exception):
    pass


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


@dataclass
class ParsedTable:
    name: str
    start_row: int
    header: list[str] | None
    rows: list[list[object]] = field(default_factory=list)
    preamble: list[list[object]] = field(default_factory=list)

    def row_ref(self, index: int) -> str:
        return f"{self.name}!R{self.start_row + index}"

    def to_state(self) -> dict:
        return {
            "name": self.name,
            "start_row": self.start_row,
            "header": self.header,
            "rows": self.rows,
            "preamble": self.preamble,
        }

    @classmethod
    def from_state(cls, data: dict) -> "ParsedTable":
        return cls(
            name=data["name"],
            start_row=data["start_row"],
            header=data.get("header"),
            rows=data.get("rows", []),
            preamble=data.get("preamble", []),
        )


@dataclass
class ParsedText:
    name: str
    text: str

    def to_state(self) -> dict:
        return {"name": self.name, "text": self.text}

    @classmethod
    def from_state(cls, data: dict) -> "ParsedText":
        return cls(name=data["name"], text=data["text"])


@dataclass
class ParsedDocument:
    source_path: str
    mime: str
    hash: str
    tables: list[ParsedTable] = field(default_factory=list)
    texts: list[ParsedText] = field(default_factory=list)

    def to_state(self) -> dict:
        return {
            "source_path": self.source_path,
            "mime": self.mime,
            "hash": self.hash,
            "tables": [table.to_state() for table in self.tables],
            "texts": [text.to_state() for text in self.texts],
        }

    @classmethod
    def from_state(cls, data: dict) -> "ParsedDocument":
        return cls(
            source_path=data["source_path"],
            mime=data["mime"],
            hash=data["hash"],
            tables=[ParsedTable.from_state(item) for item in data.get("tables", [])],
            texts=[ParsedText.from_state(item) for item in data.get("texts", [])],
        )

    @property
    def is_empty(self) -> bool:
        return not self.tables and not self.texts

    def redact(self, terms: list[str]) -> tuple["ParsedDocument", list[dict]]:
        """Return a redacted copy plus per-section hit records."""
        hits: list[Hit] = []
        redacted_texts: list[ParsedText] = []
        redacted_tables: list[ParsedTable] = []

        for text in self.texts:
            cleaned, found = redact_text(text.text, terms)
            hits.extend(found)
            redacted_texts.append(ParsedText(name=text.name, text=cleaned))

        for table in self.tables:
            header = table.header
            preamble = table.preamble
            rows = table.rows
            if header:
                cleaned, found = redact_text(" | ".join(str(cell) for cell in header), terms)
                hits.extend(found)
                header = [part.strip() for part in cleaned.split("|")]
            preamble = _redact_grid(preamble, terms, hits)
            rows = _redact_grid(rows, terms, hits)
            redacted_tables.append(ParsedTable(
                name=table.name,
                start_row=table.start_row,
                header=header,
                rows=rows,
                preamble=preamble,
            ))

        return ParsedDocument(
            source_path=self.source_path,
            mime=self.mime,
            hash=self.hash,
            tables=redacted_tables,
            texts=redacted_texts,
        ), merge_hits(hits)

    def render_for_llm(self, max_rows_per_table: int = 200, max_chars: int = 24000) -> str:
        parts: list[str] = []
        for table in self.tables:
            if table.preamble:
                parts.append(f"### {table.name} (header rows)")
                for row in table.preamble:
                    line = " | ".join("" if cell is None else str(cell) for cell in row)
                    if line.strip(" |"):
                        parts.append(line)
            parts.append(f"### {table.name} (data starts at row {table.start_row})")
            if table.header:
                parts.append(" | ".join(str(cell) for cell in table.header))
            shown = table.rows[:max_rows_per_table]
            for index, row in enumerate(shown):
                parts.append(f"[R{table.start_row + index}] " + " | ".join("" if cell is None else str(cell) for cell in row))
            if len(table.rows) > len(shown):
                parts.append(f"... {len(table.rows) - len(shown)} more rows truncated ...")
        for text in self.texts:
            parts.append(f"### {text.name}")
            parts.append(text.text)
        rendered = "\n".join(parts)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + "\n... truncated ..."
        return rendered


def _redact_grid(grid: list[list[object]], terms: list[str], hits: list[Hit]) -> list[list[object]]:
    redacted: list[list[object]] = []
    for row in grid:
        new_row: list[object] = []
        for cell in row:
            if cell is None:
                new_row.append(None)
                continue
            cleaned, found = redact_text(str(cell), terms)
            hits.extend(found)
            new_row.append(cleaned)
        redacted.append(new_row)
    return redacted


def supports_indexing(path: str | Path) -> bool:
    return Path(path).suffix.lower() in INDEXABLE_SUFFIXES


def load_document(path: str | Path) -> ParsedDocument:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    suffix = source.suffix.lower()
    mime = MIME_BY_SUFFIX.get(suffix)
    if mime is None:
        raise UnsupportedFormatError(
            f"unsupported format '{suffix}' - supported: {sorted(MIME_BY_SUFFIX)}"
        )
    if suffix not in INDEXABLE_SUFFIXES:
        raise UnsupportedFormatError(
            f"'{suffix}' is catalog-only in v2 (parse it with a converter first)"
        )

    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    if suffix in {".xlsx", ".xlsm"}:
        from ppm.loaders.excel import parse_workbook

        tables, texts = parse_workbook(source), []
    elif suffix == ".docx":
        from ppm.loaders.docx import parse_docx

        tables, texts = parse_docx(source)
    elif suffix == ".pptx":
        from ppm.loaders.pptx import parse_pptx

        tables, texts = parse_pptx(source)
    else:
        from ppm.loaders.pdf import parse_pdf

        tables, texts = parse_pdf(source)

    return ParsedDocument(source_path=str(source), mime=mime, hash=digest, tables=tables, texts=texts)


def document_stats(parsed: ParsedDocument) -> dict:
    return {
        "tables": len(parsed.tables),
        "text_blocks": len(parsed.texts),
        "table_rows": sum(len(table.rows) for table in parsed.tables),
        "text_chars": sum(len(text.text) for text in parsed.texts),
    }


def as_json(value: object) -> str:
    return json.dumps(value, default=str)
