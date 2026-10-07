"""PDF loader (pdfplumber). Pages become ParsedText; extracted tables become
ParsedTables named "page N table M" so provenance survives to the rate rows."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from ppm.loaders import ParsedTable, ParsedText, _jsonable

MAX_PAGES = 200


def parse_pdf(path: Path) -> tuple[list[ParsedTable], list[ParsedText]]:
    tables: list[ParsedTable] = []
    texts: list[ParsedText] = []
    with pdfplumber.open(path) as pdf:
        for page_index, page in enumerate(pdf.pages[:MAX_PAGES]):
            page_name = f"page {page_index + 1}"
            text = page.extract_text() or ""
            if text.strip():
                texts.append(ParsedText(name=page_name, text=text))
            for table_index, raw_table in enumerate(page.extract_tables() or []):
                grid = [[_jsonable(cell) for cell in row] for row in raw_table]
                grid = [row for row in grid if any(cell not in (None, "") for cell in row)]
                if not grid:
                    continue
                header = ["" if c is None else str(c) for c in grid[0]]
                tables.append(ParsedTable(
                    name=f"{page_name} table {table_index + 1}",
                    start_row=2,
                    header=header,
                    rows=grid[1:],
                ))
    return tables, texts
