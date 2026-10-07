"""Excel loader (openpyxl). Cost plans are workbooks; each sheet becomes a
ParsedTable with a detected header row and plain, JSON-safe rows."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from ppm.loaders import ParsedTable, _jsonable

MIN_HEADER_CELLS = 2
SCAN_HEADER_ROWS = 30


def _find_header(grid: list[list[object]]) -> int | None:
    for index, row in enumerate(grid[:SCAN_HEADER_ROWS]):
        filled = [cell for cell in row if cell not in (None, "")]
        if len(filled) >= MIN_HEADER_CELLS and any(isinstance(cell, str) for cell in filled):
            return index
    return None


def parse_workbook(path: Path) -> list[ParsedTable]:
    workbook = openpyxl.load_workbook(filename=path, read_only=True, data_only=True)
    tables: list[ParsedTable] = []
    try:
        for sheet in workbook.worksheets:
            grid = [[_jsonable(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
            grid = _trim_empty(grid)
            if not grid:
                continue
            header_index = _find_header(grid)
            if header_index is None:
                tables.append(ParsedTable(name=sheet.title, start_row=1, header=None, rows=grid))
                continue
            header = ["" if c is None else str(c) for c in grid[header_index]]
            data_rows = grid[header_index + 1:]
            preamble = [row for row in grid[:header_index] if any(cell not in (None, "") for cell in row)]
            tables.append(ParsedTable(
                name=sheet.title,
                start_row=header_index + 2,
                header=header,
                rows=data_rows,
                preamble=preamble,
            ))
    finally:
        workbook.close()
    return tables


def _trim_empty(grid: list[list[object]]) -> list[list[object]]:
    while grid and all(cell in (None, "") for cell in grid[-1]):
        grid.pop()
    while grid and all(row[0] in (None, "") for row in grid):
        grid = [row[1:] for row in grid]
    return grid
