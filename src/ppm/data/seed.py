"""Seed structured project data from the office sample workbooks.

Every extracted value keeps a sheet/cell reference. Parsing is deterministic;
no model is involved. Re-running is idempotent: projects and plans are upserted,
and lines/rates for a plan are replaced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from ppm import db
from ppm.config import get_settings
from ppm.corpus.manifest import load_manifest

PLACEHOLDERS = {
    "", "-", "n/a", "na", "none", "null", "#ref!", "#value!", "#div/0!",
    "included in mep works", "tbc", "£z", "xx", "dd/mm/yyyy", "dd/mm/yy",
    "register auto updates ccf", "guide",
}


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

DESCRIPTION_FIXES = {
    "buider's works": "Builder's Works",
    "buiders works": "Builder's Works",
    "builder works": "Builder's Works",
}


def norm(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def clean_description(value: object) -> str:
    text = norm(value)
    return DESCRIPTION_FIXES.get(text.lower(), text)


def to_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in PLACEHOLDERS:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", ".."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def to_int(value: object, low: int | None = None, high: int | None = None) -> int | None:
    number = to_number(value)
    if number is None:
        return None
    integer = int(round(number))
    if low is not None and integer < low:
        return None
    if high is not None and integer > high:
        return None
    return integer


def to_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = norm(value)
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%b-%Y", "%d %B %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def currency_of(text: str) -> str | None:
    upper = text.upper()
    for code in ("IDR", "SGD", "USD", "GBP", "EUR", "MYR"):
        if code in upper:
            return code
    if "S$" in upper:
        return "SGD"
    if "US$" in upper or "USD$" in upper:
        return "USD"
    if "RP" in upper:
        return "IDR"
    return None


def days_of(text: str) -> int | None:
    lowered = norm(text).lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(weeks?|wks?|w)\b", lowered)
    if match:
        return int(round(float(match.group(1)) * 7))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(days?|d)\b", lowered)
    if match:
        return int(round(float(match.group(1))))
    match = re.search(r"(\d+(?:\.\d+)?)\s*month", lowered)
    if match:
        return int(round(float(match.group(1)) * 30))
    return None


def map_columns(header: list[object], rules: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """Map the first unused column whose header contains any keyword."""
    normalized = [norm(cell).lower() for cell in header]
    used: set[int] = set()
    mapping: dict[str, int] = {}
    for field_name, keywords in rules.items():
        for index, cell in enumerate(normalized):
            if index in used or not cell:
                continue
            if any(keyword in cell for keyword in keywords):
                mapping[field_name] = index
                used.add(index)
                break
    return mapping


def _row_value(row: list[object], index: int | None) -> object:
    if index is None or index >= len(row):
        return None
    return row[index]


# ---------------------------------------------------------------------------
# Parsed records
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkComponent:
    element_code: str | None
    description: str
    amount: float
    rate: float | None
    source_ref: str


@dataclass
class BenchmarkProject:
    name: str
    gfa_m2: float | None
    total_cost: float | None
    currency: str | None
    plan_date: date | None
    components: list[BenchmarkComponent] = field(default_factory=list)
    source_ref: str = ""
    sector: str = "commercial"


@dataclass
class RiskRow:
    risk_ref: str | None
    category: str | None
    description: str
    likelihood: int | None
    impact: int | None
    cost_impact: float | None
    programme_impact: str | None
    owner: str | None
    status: str | None
    review_date: date | None
    mitigation: str | None
    source_ref: str


@dataclass
class ChangeRow:
    change_ref: str | None
    description: str
    cause: str | None
    cost_impact: float | None
    cost_impact_agreed: float | None
    cost_impact_fees: float | None
    drawdown_from: str | None
    time_impact_days: int | None
    status: str | None
    instruction_ref: str | None
    raised_date: date | None
    decided_date: date | None
    source_ref: str


@dataclass
class SeedStats:
    projects: int = 0
    cost_plans: int = 0
    cost_lines: int = 0
    benchmark_rates: int = 0
    risks: int = 0
    changes: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "SeedStats") -> None:
        self.projects += other.projects
        self.cost_plans += other.cost_plans
        self.cost_lines += other.cost_lines
        self.benchmark_rates += other.benchmark_rates
        self.risks += other.risks
        self.changes += other.changes
        self.skipped += other.skipped
        self.warnings.extend(other.warnings)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _read_rows(sheet, max_row: int | None = None) -> list[list[object]]:
    return [list(row) for row in sheet.iter_rows(values_only=True, max_row=max_row)]


def parse_benchmark(path: Path, label: str) -> list[BenchmarkProject]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if "Cost Model" not in workbook.sheetnames:
            return []
        sheet = workbook["Cost Model"]
        rows = _read_rows(sheet, max_row=80)

        header_index = next(
            (index for index, row in enumerate(rows)
             if any("cost component" in norm(cell).lower() for cell in row)),
            None,
        )
        if header_index is None:
            return []
        header = rows[header_index]

        pairs: list[tuple[str, int, int]] = []
        index = 2
        project_number = 1
        while index < len(header) - 1:
            name = norm(header[index])
            if name and "average" not in name.lower() and name not in {"%", "no.", "cost component"}:
                pairs.append((f"Project {project_number}", index, index + 1))
                project_number += 1
                index += 2
            else:
                index += 1
        if not pairs:
            return []

        gfa_row = next(
            (row for row in rows[header_index + 1:]
             if any("gfa" in norm(cell).lower() for cell in row[:4])),
            None,
        )
        total_index = next(
            (index for index, row in enumerate(rows)
             if index > header_index and any("total project cost" in norm(cell).lower() for cell in row[:3])),
            None,
        )
        if total_index is None:
            total_index = len(rows) - 1
        total_row = rows[total_index]
        currency = currency_of(" ".join(norm(cell) for cell in total_row))
        plan_date = None
        if "Cover" in workbook.sheetnames:
            for row in _read_rows(workbook["Cover"], max_row=8):
                for cell in row:
                    candidate = to_date(cell)
                    if candidate:
                        plan_date = candidate
                        break
                if plan_date:
                    break

        projects: list[BenchmarkProject] = []
        for name, amount_col, rate_col in pairs:
            gfa = to_number(_row_value(gfa_row, rate_col)) if gfa_row else None
            if gfa is None and gfa_row is not None:
                gfa = to_number(_row_value(gfa_row, amount_col))
            total = to_number(_row_value(total_row, amount_col))
            project = BenchmarkProject(
                name=f"Benchmark {label} - {name}",
                gfa_m2=gfa,
                total_cost=total,
                currency=currency,
                plan_date=plan_date,
                source_ref=f"Cost Model!R{total_index + 1}:{get_column_letter(amount_col + 1)}",
            )
            for row_index in range(header_index + 2, total_index):
                row = rows[row_index]
                description = clean_description(_row_value(row, 1))
                if not description or description.lower().startswith("total"):
                    continue
                amount = to_number(_row_value(row, amount_col))
                if amount is None:
                    continue
                element_code = norm(_row_value(row, 0)) or None
                project.components.append(BenchmarkComponent(
                    element_code=element_code,
                    description=description,
                    amount=amount,
                    rate=(amount / gfa) if gfa else None,
                    source_ref=f"Cost Model!R{row_index + 1}:{get_column_letter(amount_col + 1)}",
                ))
            projects.append(project)
        return projects
    finally:
        workbook.close()


def parse_risk_register(path: Path) -> list[RiskRow]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if "Risk Register" not in workbook.sheetnames:
            return []
        rows = _read_rows(workbook["Risk Register"], max_row=200)
        header_index = next(
            (index for index, row in enumerate(rows)
             if any("risk id" in norm(cell).lower() for cell in row)
             and any("probability" in norm(cell).lower() for cell in row)),
            None,
        )
        if header_index is None:
            return []
        mapping = map_columns(rows[header_index], {
            "risk_id": ("risk id",),
            "category": ("category",),
            "title": ("risk title",),
            "narrative": ("risk description", "narrative"),
            "consequences": ("consequences",),
            "status": ("status",),
            "cost_impact": ("cost impact",),
            "programme_impact": ("programme impact",),
            "likelihood": ("probability",),
            "impact": ("impact",),
            "rating": ("risk rating",),
            "mitigation": ("mitigating action",),
            "owner": ("risk owner",),
            "review_date": ("action date",),
        })

        parsed: list[RiskRow] = []
        for index in range(header_index + 2, len(rows)):
            row = rows[index]
            title = norm(_row_value(row, mapping.get("title")))
            narrative = norm(_row_value(row, mapping.get("narrative")))
            consequences = norm(_row_value(row, mapping.get("consequences")))
            description = " | ".join(part for part in (title, narrative, consequences) if part)
            if not description:
                continue
            likelihood = to_int(_row_value(row, mapping.get("likelihood")), 1, 5)
            impact = to_int(_row_value(row, mapping.get("impact")), 1, 5)
            # Rows below the actual register often contain notes or a second
            # table with single-letter placeholders. Keep only rated risks.
            if likelihood is None and impact is None:
                continue
            parsed.append(RiskRow(
                risk_ref=norm(_row_value(row, mapping.get("risk_id"))) or None,
                category=norm(_row_value(row, mapping.get("category"))) or None,
                description=description,
                likelihood=likelihood,
                impact=impact,
                cost_impact=to_number(_row_value(row, mapping.get("cost_impact"))),
                programme_impact=norm(_row_value(row, mapping.get("programme_impact"))) or None,
                owner=norm(_row_value(row, mapping.get("owner"))) or None,
                status=norm(_row_value(row, mapping.get("status"))) or None,
                review_date=to_date(_row_value(row, mapping.get("review_date"))),
                mitigation=norm(_row_value(row, mapping.get("mitigation"))) or None,
                source_ref=f"Risk Register!R{index + 1}",
            ))
        return parsed
    finally:
        workbook.close()


def parse_change_register(path: Path) -> list[ChangeRow]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if "CC Register" not in workbook.sheetnames:
            return []
        rows = _read_rows(workbook["CC Register"], max_row=500)
        header_index = next(
            (index for index, row in enumerate(rows)
             if any("cc no" in norm(cell).lower() for cell in row)
             and any("proposed change" in norm(cell).lower() for cell in row)),
            None,
        )
        if header_index is None:
            return []
        mapping = map_columns(rows[header_index], {
            "change_ref": ("cc no",),
            "raised_date": ("date raised",),
            "building": ("building",),
            "description": ("proposed change",),
            "cause": ("reasons for change",),
            "originator": ("originator",),
            "design_stage": ("design stage",),
            "method": ("method of request",),
            "change_type": ("change type",),
            "cost_impact": ("cost impact qs",),
            "cost_agreed": ("cost impact agreed",),
            "cost_fees": ("cost impact fees",),
            "drawdown": ("drawdown",),
            "time_assessed": ("time impact assessment",),
            "time_agreed": ("time impact agreed",),
            "date_issued": ("date issued",),
            "date_decision": ("date decision",),
            "signoff": ("actual sign off",),
            "status": ("change status",),
            "pmi": ("pmi ref",),
        })

        parsed: list[ChangeRow] = []
        for index in range(header_index + 2, len(rows)):
            row = rows[index]
            description = norm(_row_value(row, mapping.get("description")))
            if not description or description.lower() in PLACEHOLDERS:
                continue
            if description.lower().startswith(("enter details", "register automatically")):
                continue
            change_ref = norm(_row_value(row, mapping.get("change_ref"))) or None
            parsed.append(ChangeRow(
                change_ref=change_ref,
                description=description,
                cause=norm(_row_value(row, mapping.get("cause"))) or None,
                cost_impact=to_number(_row_value(row, mapping.get("cost_impact"))),
                cost_impact_agreed=to_number(_row_value(row, mapping.get("cost_agreed"))),
                cost_impact_fees=to_number(_row_value(row, mapping.get("cost_fees"))),
                drawdown_from=norm(_row_value(row, mapping.get("drawdown"))) or None,
                time_impact_days=days_of(str(_row_value(row, mapping.get("time_assessed")) or "")),
                status=norm(_row_value(row, mapping.get("status"))) or None,
                instruction_ref=norm(_row_value(row, mapping.get("pmi") or mapping.get("change_ref"))) or None,
                raised_date=to_date(_row_value(row, mapping.get("raised_date"))),
                decided_date=to_date(_row_value(row, mapping.get("signoff"))),
                source_ref=f"CC Register!R{index + 1}",
            ))
        return parsed
    finally:
        workbook.close()


def parse_cost_plan_detail(path: Path) -> tuple[list[dict], str | None, date | None]:
    """Generic Section 4 parser. Returns (lines, currency, plan_date)."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Section 4"] if "Section 4" in workbook.sheetnames else None
        if sheet is None:
            return [], None, None
        rows = _read_rows(sheet, max_row=3000)
        header_index = next(
            (index for index, row in enumerate(rows[:20])
             if any("description" in norm(cell).lower() for cell in row)
             and any("qty" in norm(cell).lower() for cell in row)),
            None,
        )
        if header_index is None:
            return [], None, None
        header = rows[header_index]
        mapping = map_columns(header, {
            "code": ("ref", "code"),
            "description": ("description",),
            "qty": ("qty", "quantity"),
            "unit": ("unit",),
            "rate": ("rate",),
            "amount": ("amount", "total"),
        })
        currency = currency_of(" ".join(norm(cell) for cell in header))
        lines: list[dict] = []
        for index in range(header_index + 1, len(rows)):
            row = rows[index]
            description = norm(_row_value(row, mapping.get("description")))
            if not description:
                continue
            qty = to_number(_row_value(row, mapping.get("qty")))
            rate = to_number(_row_value(row, mapping.get("rate")))
            amount = to_number(_row_value(row, mapping.get("amount")))
            if amount is None and rate is not None and qty is not None:
                amount = rate * qty
            if amount is None or amount == 0:
                continue
            lines.append({
                "wbs_code": norm(_row_value(row, mapping.get("code"))) or None,
                "element_code": norm(_row_value(row, mapping.get("code"))) or None,
                "description": description,
                "qty": qty,
                "unit": norm(_row_value(row, mapping.get("unit"))) or None,
                "rate": rate if rate is not None else ((amount / qty) if qty else None),
                "amount": amount,
                "currency": currency,
                "source_ref": f"Section 4!R{index + 1}",
                "confidence": 0.9 if qty and (rate or amount) else 0.6,
            })
        return lines, currency, None
    finally:
        workbook.close()


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------

def _document_id(path: Path) -> str | None:
    row = db.fetch_one("SELECT document_id FROM document WHERE source_path = %s LIMIT 1", (str(path),))
    if not row:
        row = db.fetch_one(
            "SELECT document_id FROM document WHERE source_path ILIKE %s LIMIT 1",
            (f"%{path.name}",),
        )
    return str(row["document_id"]) if row else None


def _upsert_project(cur, name: str, *, sector=None, stage=None, currency=None,
                    gfa_m2=None, area_basis=None, document_id=None, source_ref=None,
                    data_class: str = "baseline") -> str:
    cur.execute(
        "INSERT INTO project (name, sector, stage, status, currency, gfa_m2, area_basis, "
        "source_document_id, source_ref, data_class) VALUES (%s, %s, %s, 'sample', %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET sector = COALESCE(EXCLUDED.sector, project.sector), "
        "stage = COALESCE(EXCLUDED.stage, project.stage), currency = COALESCE(EXCLUDED.currency, project.currency), "
        "gfa_m2 = COALESCE(EXCLUDED.gfa_m2, project.gfa_m2), "
        "area_basis = COALESCE(EXCLUDED.area_basis, project.area_basis), "
        "source_document_id = COALESCE(EXCLUDED.source_document_id, project.source_document_id), "
        "source_ref = COALESCE(EXCLUDED.source_ref, project.source_ref), "
        "data_class = EXCLUDED.data_class RETURNING project_id",
        (name, sector, stage, currency, gfa_m2, area_basis, document_id, source_ref, data_class),
    )
    return str(cur.fetchone()[0])


def _upsert_cost_plan(cur, project_id: str, version: int, *, stage, plan_date, currency,
                      total_cost, gfa_m2, area_basis, rate_basis, document_id,
                      source_ref, confidence) -> str:
    cur.execute(
        "INSERT INTO cost_plan (project_id, version, stage, plan_date, currency, total_cost, "
        "gfa_m2, area_basis, rate_basis, document_id, source_ref, confidence) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (project_id, version) DO UPDATE SET stage = EXCLUDED.stage, "
        "plan_date = EXCLUDED.plan_date, currency = EXCLUDED.currency, total_cost = EXCLUDED.total_cost, "
        "gfa_m2 = EXCLUDED.gfa_m2, area_basis = EXCLUDED.area_basis, rate_basis = EXCLUDED.rate_basis, "
        "document_id = EXCLUDED.document_id, source_ref = EXCLUDED.source_ref, "
        "confidence = EXCLUDED.confidence RETURNING costplan_id",
        (project_id, version, stage, plan_date, currency, total_cost, gfa_m2, area_basis,
         rate_basis, document_id, source_ref, confidence),
    )
    costplan_id = str(cur.fetchone()[0])
    cur.execute("DELETE FROM cost_line WHERE costplan_id = %s", (costplan_id,))
    cur.execute("DELETE FROM benchmark_rate WHERE costplan_id = %s", (costplan_id,))
    return costplan_id


def _insert_structure(project: BenchmarkProject, label: str, path: Path, stats: SeedStats, dry_run: bool) -> None:
    stats.projects += 1
    stats.cost_lines += len(project.components)
    stats.benchmark_rates += len(project.components)
    if dry_run:
        return
    document_id = _document_id(path)
    with db.connection() as conn, conn.cursor() as cur:
        project_id = _upsert_project(
            cur, project.name, sector=project.sector, stage="design",
            currency=project.currency, gfa_m2=project.gfa_m2, area_basis="GFA",
            document_id=document_id, source_ref=project.source_ref,
            data_class="restricted" if project.name.startswith("Benchmark B -") else "baseline",
        )
        costplan_id = _upsert_cost_plan(
            cur, project_id, 1, stage="design", plan_date=project.plan_date,
            currency=project.currency, total_cost=project.total_cost, gfa_m2=project.gfa_m2,
            area_basis="GFA", rate_basis="all_in", document_id=document_id,
            source_ref=project.source_ref, confidence=0.9,
        )
        stats.cost_plans += 1
        for component in project.components:
            cur.execute(
                "INSERT INTO cost_line (costplan_id, wbs_code, element_code, description, qty, unit, "
                "rate, amount, currency, is_subtotal, source_ref, confidence) "
                "VALUES (%s, %s, %s, %s, %s, 'm2', %s, %s, %s, false, %s, 0.9) "
                "ON CONFLICT (costplan_id, source_ref) DO UPDATE SET description = EXCLUDED.description, "
                "amount = EXCLUDED.amount, rate = EXCLUDED.rate",
                (costplan_id, component.element_code, component.element_code, component.description,
                 project.gfa_m2, component.rate, component.amount, project.currency, component.source_ref),
            )
            cur.execute(
                "INSERT INTO benchmark_rate (costplan_id, project_id, element_code, description, rate, "
                "unit, date_normalised_to, location, sector, spec_level, client_identifiable, source_ref) "
                "VALUES (%s, %s, %s, %s, %s, 'm2', %s, %s, %s, NULL, false, %s) "
                "ON CONFLICT (costplan_id, description, source_ref) DO NOTHING",
                (costplan_id, project_id, component.element_code, component.description,
                 component.rate, project.plan_date, "unknown", project.sector, component.source_ref),
            )


def _insert_risks(rows: list[RiskRow], path: Path, stats: SeedStats, dry_run: bool) -> None:
    stats.risks += len(rows)
    if dry_run or not rows:
        return
    document_id = _document_id(path)
    with db.connection() as conn, conn.cursor() as cur:
        project_id = _upsert_project(
            cur, "Risk Register Template (sample)", sector="commercial", stage="design",
            document_id=document_id, source_ref="Risk Register!R3",
        )
        cur.execute("DELETE FROM risk WHERE project_id = %s", (project_id,))
        for row in rows:
            cur.execute(
                "INSERT INTO risk (project_id, risk_ref, category, description, likelihood, impact, "
                "cost_impact, programme_impact, owner, status, review_date, mitigation, source_ref, document_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (project_id, row.risk_ref, row.category, row.description, row.likelihood, row.impact,
                 row.cost_impact, row.programme_impact, row.owner, row.status, row.review_date,
                 row.mitigation, row.source_ref, document_id),
            )


def _insert_changes(rows: list[ChangeRow], path: Path, stats: SeedStats, dry_run: bool) -> None:
    stats.changes += len(rows)
    if dry_run or not rows:
        return
    document_id = _document_id(path)
    with db.connection() as conn, conn.cursor() as cur:
        project_id = _upsert_project(
            cur, "Change Register Template (sample)", sector="commercial", stage="construction",
            document_id=document_id, source_ref="CC Register!R8",
        )
        cur.execute("DELETE FROM change_event WHERE project_id = %s", (project_id,))
        for row in rows:
            cur.execute(
                "INSERT INTO change_event (project_id, change_ref, description, cause, cost_impact, "
                "cost_impact_agreed, cost_impact_fees, drawdown_from, time_impact_days, status, "
                "instruction_ref, raised_date, decided_date, source_ref, document_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (project_id, row.change_ref, row.description, row.cause, row.cost_impact,
                 row.cost_impact_agreed, row.cost_impact_fees, row.drawdown_from, row.time_impact_days,
                 row.status, row.instruction_ref, row.raised_date, row.decided_date, row.source_ref,
                 document_id),
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

DEFAULT_FILES = [
    ("benchmark", "A", "00. Template T&T/CM/CM/Brilliant basics template/310 - Basic template/313 - Cost Model & Benchmarking - Best Practice - Julius Bar.xlsx"),
    ("benchmark", "B", "00. Template T&T/CM/CM/Brilliant basics template/310 - Basic template/313 - Cost Model & Benchmarking - Best Practice - Chevron.xlsx"),
    ("risk", None, "00. Template T&T/CM/CM/Brilliant basics template/310 - Basic template/311 - Risk Register - Template.xlsx"),
    ("change", None, "00. Template T&T/CM/CM/Brilliant basics template/360 - Post Contract/365 - Change Control Register Template.xlsx"),
    ("costplan", None, "00. Template T&T/CM/CM/Brilliant basics template/310 - Basic template/314 - Cost Plan - Template.xlsx"),
]


def seed_defaults(dry_run: bool = False) -> SeedStats:
    settings = get_settings()
    manifest = load_manifest(settings.manifest_path)
    base_dir = Path(manifest.get("base_dir", ".")).expanduser().resolve()
    stats = SeedStats()

    # The zero-line template plan is not project data; remove any previous copy.
    if not dry_run:
        db.execute("DELETE FROM project WHERE name = 'Cost Plan Template (synthetic)'")

    for kind, label, relative in DEFAULT_FILES:
        path = base_dir / relative
        if not path.is_file():
            stats.warnings.append(f"missing source: {path}")
            continue
        try:
            if kind == "benchmark":
                projects = parse_benchmark(path, label)
                if not projects:
                    stats.warnings.append(f"no benchmark projects parsed: {path.name}")
                for project in projects:
                    _insert_structure(project, label, path, stats, dry_run)
            elif kind == "risk":
                rows = parse_risk_register(path)
                _insert_risks(rows, path, stats, dry_run)
            elif kind == "change":
                rows = parse_change_register(path)
                _insert_changes(rows, path, stats, dry_run)
            elif kind == "costplan":
                lines, currency, plan_date = parse_cost_plan_detail(path)
                stats.cost_lines += len(lines)
                if dry_run:
                    continue
                if lines:
                    document_id = _document_id(path)
                    with db.connection() as conn, conn.cursor() as cur:
                        project_id = _upsert_project(
                            cur, "Cost Plan Template (synthetic)", sector="commercial",
                            stage="design", currency=currency, document_id=document_id,
                            source_ref="Section 4!R3",
                        )
                        total = sum(line["amount"] or 0 for line in lines)
                        costplan_id = _upsert_cost_plan(
                            cur, project_id, 1, stage="design", plan_date=plan_date,
                            currency=currency, total_cost=total, gfa_m2=None,
                            area_basis=None, rate_basis=None, document_id=document_id,
                            source_ref="Section 4!R3", confidence=0.7,
                        )
                        stats.cost_plans += 1
                        for line in lines:
                            cur.execute(
                                "INSERT INTO cost_line (costplan_id, wbs_code, element_code, description, qty, "
                                "unit, rate, amount, currency, is_subtotal, source_ref, confidence) "
                                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s, %s) "
                                "ON CONFLICT (costplan_id, source_ref) DO NOTHING",
                                (costplan_id, line["wbs_code"], line["element_code"], line["description"],
                                 line["qty"], line["unit"], line["rate"], line["amount"],
                                 line["currency"], line["source_ref"], line["confidence"]),
                            )
                else:
                    stats.skipped += 1
                    stats.warnings.append(
                        f"{path.name}: no detail lines (template only); cost plan not seeded"
                    )
        except Exception as exc:  # one bad workbook must not stop the seed
            stats.warnings.append(f"{path.name}: {exc}")

    return stats
