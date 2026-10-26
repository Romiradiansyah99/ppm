"""Filename parsing and deterministic classification of templates.

Order matters: the first matching rule wins, so specific titles must appear
before generic ones (a "close out report" is closeout, not just a report).
"""

from __future__ import annotations

import re
from pathlib import Path

_CODE_RE = re.compile(r"^(?P<code>\d{1,3}[a-z]?)\s*-\s*(?P<title>.+)$", re.IGNORECASE)

_STAGE_BY_FOLDER = {
    "0. preparation": "preparation",
    "1. design": "design",
    "2. preconstruction": "preconstruction",
    "3. procurement": "procurement",
    "4. construction": "construction",
    "5. close-out": "closeout",
    "5. closeout": "closeout",
    "6. governance & assurance": "governance",
    "7. others": "others",
    "310 - basic template": "unclassified",
    "320 - pre qualification": "procurement",
    "330 - tender document": "procurement",
    "340 - tender process": "procurement",
    "360 - post contract": "construction",
    "370 - close out": "closeout",
    "construction supervision": "construction",
    "cm template": "unclassified",
    "brilliant basics template": "unclassified",
}

_STAGE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("closeout", ("close out", "close-out", "handover", "practical completion", "defect",
                  "punch list", "final account", "final meeting", "statement of final")),
    ("procurement", ("procurement", "pre-qualification", "prequalification", "pqq",
                     "tender", "contractor recommendation", "rfq")),
    ("preparation", ("start up", "start-up", "brief", "stakeholder", "project directory",
                     "folder structure", "lessons learnt", "lessons learned",
                     "employers requirements", "design responsibility", "commissioning management")),
    ("design", ("design", "room data", "drawing", "transmittal", "rfi",
                "value engineering", "concept", "stage report")),
    ("preconstruction", ("pep", "project execution plan", "feasibility", "pre-construction")),
    ("construction", ("construction", "progress", "site ", "monthly report", "dashboard",
                      "ncr", "itp", "inspection", "quality", "supervision", "daily report",
                      "weekly", "meeting")),
    ("governance", ("risk", "change control", "issues", "audit", "action register",
                    "sor", "governance", "hse", "statement of requirement")),
]

_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("pep", ("pep", "project execution plan")),
    ("brief", ("brief",)),
    ("cost_plan", ("cost plan", "cost model", "benchmark", "cost monitoring")),
    ("cost_report", ("cost report", "monthly cost", "interim valuation", "statement of final account")),
    ("cash_flow", ("cash flow",)),
    ("value_engineering", ("value engineering",)),
    ("risk", ("risk",)),
    ("change", ("change control", "variation", "change register")),
    ("issues", ("issues log", "issue")),
    ("procurement", ("procurement", "pre-qualification", "prequalification", "pqq",
                     "contractor recommendation", "rfq")),
    ("tender", ("tender",)),
    ("meeting", ("meeting", "agenda", "minutes", "mom")),
    ("checklist", ("checklist",)),
    ("register", ("register", "log")),
    ("dashboard", ("dashboard",)),
    ("quality", ("quality", "ncr", "itp", "inspection", "site walk", "qpp")),
    ("handover", ("handover", "close out", "close-out", "practical completion", "defect",
                  "punch list", "final account")),
    ("lessons", ("lessons learnt", "lessons learned")),
    ("report", ("report", "monthly reporting")),
]


def parse_title(file_name: str) -> str:
    stem = Path(file_name).stem
    match = _CODE_RE.match(stem)
    title = match.group("title") if match else stem
    return " ".join(title.replace("_", " ").replace("+", " ").split()).strip()


def parse_code(file_name: str) -> str | None:
    match = _CODE_RE.match(Path(file_name).stem)
    return match.group("code") if match else None


def infer_stage(relative_path: str, title: str) -> str:
    parts = [part for part in Path(relative_path).parts[:-1]]
    for part in parts:
        mapped = _STAGE_BY_FOLDER.get(part.lower().strip())
        if mapped:
            return mapped
    lowered = title.lower()
    for stage, keywords in _STAGE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return stage
    return "unclassified"


def infer_category(title: str) -> str:
    lowered = title.lower()
    for category, keywords in _CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "other"


def read_catalog_rows(path: str | Path, max_rows: int = 2000) -> list[dict[str, str | None]]:
    """Tolerant reader for the office "List of Templates" workbooks.

    The catalogs are hand-maintained and differ between offices, so the parser
    looks for a header row containing a code-like and a title-like column and
    then reads whatever rows follow. Unreadable catalogs return an empty list.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(filename=path, read_only=True, data_only=True)
    rows: list[dict[str, str | None]] = []
    try:
        for sheet in workbook.worksheets:
            grid = [
                [cell for cell in row]
                for row in sheet.iter_rows(values_only=True, max_row=max_rows)
            ]
            header_index = None
            code_col = title_col = stage_col = category_col = None
            for index, row in enumerate(grid[:40]):
                lowered = [str(cell or "").strip().lower() for cell in row]
                code_col = next(
                    (j for j, cell in enumerate(lowered)
                     if cell in {"no", "no.", "num", "number", "code", "ref", "ref no"}),
                    None,
                )
                title_col = next(
                    (j for j, cell in enumerate(lowered)
                     if any(key in cell for key in ("template", "title", "name", "document", "description"))),
                    None,
                )
                if code_col is not None and title_col is not None:
                    header_index = index
                    stage_col = next(
                        (j for j, cell in enumerate(lowered) if "stage" in cell or "phase" in cell),
                        None,
                    )
                    category_col = next(
                        (j for j, cell in enumerate(lowered) if "category" in cell or "type" in cell),
                        None,
                    )
                    break
            if header_index is None:
                continue
            for row in grid[header_index + 1:]:
                title = str(row[title_col] or "").strip() if title_col < len(row) else ""
                if not title:
                    continue
                code = str(row[code_col] or "").strip() if code_col < len(row) else ""
                rows.append({
                    "code": code or None,
                    "title": title,
                    "stage": str(row[stage_col] or "").strip() if stage_col is not None and stage_col < len(row) else None,
                    "category": str(row[category_col] or "").strip() if category_col is not None and category_col < len(row) else None,
                    "sheet": sheet.title,
                })
    finally:
        workbook.close()
    return rows
