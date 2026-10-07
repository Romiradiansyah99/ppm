"""Custom document loaders (plan section 9: build thin loaders over
pdfplumber/openpyxl - deterministic work, not ML).

ParsedDocument keeps plain rows; LangChain never sees the parse step, and the
extraction step receives a text rendering plus the raw tables.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

MIME_BY_SUFFIX = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pdf": "application/pdf",
}


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
    name: str                  # sheet name, or "page 3 table 1"
    start_row: int             # 1-based row in the source where data rows begin
    header: list[str] | None
    rows: list[list[object]] = field(default_factory=list)
    preamble: list[list[object]] = field(default_factory=list)   # rows above the header

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
            "tables": [t.to_state() for t in self.tables],
            "texts": [t.to_state() for t in self.texts],
        }

    @classmethod
    def from_state(cls, data: dict) -> "ParsedDocument":
        return cls(
            source_path=data["source_path"],
            mime=data["mime"],
            hash=data["hash"],
            tables=[ParsedTable.from_state(t) for t in data.get("tables", [])],
            texts=[ParsedText.from_state(t) for t in data.get("texts", [])],
        )

    def render_for_llm(self, max_rows_per_table: int = 200, max_chars: int = 24000) -> str:
        """Deterministic text rendering of the document for the extraction chain."""
        parts: list[str] = []
        for table in self.tables:
            if table.preamble:
                parts.append(f"### {table.name} (header rows)")
                for row in table.preamble:
                    line = " | ".join("" if c is None else str(c) for c in row)
                    if line.strip(" |"):
                        parts.append(line)
            parts.append(f"### {table.name} (data starts at row {table.start_row})")
            if table.header:
                parts.append(" | ".join(str(c) for c in table.header))
            shown = table.rows[:max_rows_per_table]
            for index, row in enumerate(shown):
                parts.append(f"[R{table.start_row + index}] " + " | ".join("" if c is None else str(c) for c in row))
            if len(table.rows) > len(shown):
                parts.append(f"... {len(table.rows) - len(shown)} more rows truncated ...")
        for text in self.texts:
            parts.append(f"### {text.name}")
            parts.append(text.text)
        rendered = "\n".join(parts)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + "\n... truncated ..."
        return rendered


def load_document(path: str | Path) -> ParsedDocument:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    suffix = source.suffix.lower()
    mime = MIME_BY_SUFFIX.get(suffix)
    if mime is None:
        raise UnsupportedFormatError(f"unsupported format '{suffix}' - supported: {sorted(MIME_BY_SUFFIX)}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    if suffix in {".xlsx", ".xlsm"}:
        from ppm.loaders.excel import parse_workbook

        tables, texts = parse_workbook(source), []
    else:
        from ppm.loaders.pdf import parse_pdf

        tables, texts = parse_pdf(source)

    return ParsedDocument(source_path=str(source), mime=mime, hash=digest, tables=tables, texts=texts)
