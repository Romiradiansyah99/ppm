"""Generate synthetic sample cost plans for the PPM pipeline.

Everything here is invented. The files deliberately speak the office's number
formats (dots as thousands separators, dd/mm/yyyy dates, mixed unit
spellings) so the normalisation path is exercised honestly. One file carries
a row that must land in the human-review queue, and one description contains
the synthetic client name so the redaction pass does something visible.

Usage: .venv\\Scripts\\python.exe scripts/make_sample_data.py [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "samples"

BANNER = "SYNTHETIC SAMPLE - NOT REAL DATA - PPM pipeline fixture"
SYNTHETIC_CLIENT = "PT SYNTHETIC CLIENT"

# Element rows: (code, description, unit, qty, rate) - amounts computed.
ALPHA_ROWS = [
    ("A10", "Site clearance and grubbing", "m2", 12500, 45000),
    ("A20", "Excavation to reduce level, ordinary soil", "m3", 3800, 125000),
    ("B10", "Pile cap concrete grade 35MPa", "m3", 420, 1450000),
    ("B20", "Reinforcement bar, cut bend and fix", "kg", 98000, 18500),
    ("C10", "Ground floor slab concrete grade 30MPa", "m2", 9400, 850000),
    ("C40", "Blockwork wall 100mm", "m2", 5600, 285000),
    ("E10", "Internal wall plaster and paint", "m2", 14200, 165000),
    ("F10", "Aluminium window unit, medium spec", "m2", 1850, 2350000),
    ("F20", "Glazed curtain wall, unitised", "m2", 3200, 4250000),
    ("G10", "Sanitaryware, medium spec", "nr", 210, 3850000),
    ("H10", "Sprinkler installation", "m2", 22000, 145000),
    ("J30", "Passenger lift, 8 person", "nr", 6, 850000000),
    ("K10", "External works, paving and drainage", "sum", 1, 4850000000),
    ("L10", "Preliminaries and site management", "sum", 1, 7200000000),
]

BETA_ROWS = [
    ("A10", "Demolition of existing structure", "m3", 2650, 210000),
    ("B10", "Bored pile 600mm diameter", "m", 4200, 1650000),
    ("B15", "Pile testing, static load", "nr", 12, 185000000),
    ("C10", "Basement raft concrete grade 40MPa", "m3", 3600, 1550000),
    ("C20", "Waterproofing, basement tanking", "m2", 11000, 385000),
    ("C30", "Reinforcement bar, cut bend and fix", "kg", 1450000, 17500),
    ("D10", "Structural steel frame, fabricated", "kg", 980000, 32000),
    ("E10", "Metal deck roof with insulation", "m2", 8800, 685000),
    ("F10", "Facade, aluminium composite panel, high spec", "m2", 6400, 3150000),
    ("G20", "Hygiene package, high spec", "nr", 96, 5800000),
    ("H10", "Electrical installation, main LV", "sum", 1, 12400000000),
    ("H20", "Fire protection, sprinkler and hydrant", "sum", 1, 5600000000),
    ("H30", "HVAC, VRF system, medium spec", "m2", 31500, 1950000),
    ("J20", "Escalator, indoor", "nr", 8, 1450000000),
    ("K10", "Landscape and external lighting", "sum", 1, 3250000000),
    ("L10", "Preliminaries, insurances and bonds", "sum", 1, 15800000000),
]

GAMMA_ROWS = [
    ("A10", "Site surveys and setting out", "sum", 1, 285000000),
    ("B10", "Continuous flight auger pile 450mm", "m", 5400, 985000),
    ("C10", "Pile cap concrete grade 35MPa", "m3", 620, 1485000),
    ("C20", "Reinforcement bar, cut bend and fix", "kg", 121000, 17800),
    ("D10", "Steel roof truss, fabricated", "kg", 385000, 29500),
    ("E10", f"{SYNTHETIC_CLIENT} head office fit-out - partitions", "m2", 8600, 545000),
    ("E20", "Suspended ceiling, mineral fibre", "m2", 10400, 195000),
    ("F10", "Window unit, low spec", "m2", 2600, 1650000),
    ("G10", "Sanitaryware, medium spec", "nr", 148, 3850000),
    ("H10", "Electrical installation, tenant fit-out", "sum", 1, 4850000000),
    ("J10", "Fire alarm and detection", "sum", 1, 875000000),
    ("K10", "External works, paving", None, None, 1850000),   # missing unit and qty: review queue
]


def write_workbook(path: Path, project: str, stage: str, plan_date: str, area_basis: str,
                   rate_basis: str, rows: list, description_style: str,
                   sector: str | None = None) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cost Plan"

    sheet.append([BANNER])
    sheet.append([f"Project: {project}"])
    sheet.append([f"Date: {plan_date}"])
    sheet.append([f"Stage: {stage}"])
    sheet.append([f"Area basis: {area_basis}"])
    sheet.append(["GFA (m2): 12500"])
    sheet.append([f"Rate basis: {rate_basis}"])
    sheet.append(["Location: Jakarta"])
    if sector:
        sheet.append([f"Sector: {sector}"])
    sheet.append([f"Client: {SYNTHETIC_CLIENT} - strip this, never index it"])
    sheet.append([])
    sheet.append(["No", "Element Code", "Description", "Unit", "Qty", "Rate (IDR)", "Amount (IDR)", "Spec Level"])

    for index, (code, description, unit, qty, rate) in enumerate(rows, start=1):
        amount = round(qty * rate) if qty is not None and rate is not None else None
        sheet.append([
            index, code, description, unit, qty,
            format_idr(rate, description_style),
            format_idr(amount, description_style),
            "medium" if index % 3 == 0 else ("high" if index % 5 == 0 else "low"),
        ])

    if len(rows) > 6:
        basis = workbook.create_sheet("Basis")
        basis.append(["Basis notes"])
        basis.append(["Synthetic fixture for the PPM ingest pipeline."])
        basis.append([f"Rate basis: {rate_basis}. Area basis: {area_basis}."])

    workbook.save(path)


def format_idr(value: float | None, style: str) -> str | None:
    if value is None:
        return None
    if style == "dots":                      # 1.234.567 (Indonesian thousands)
        return f"{value:,.0f}".replace(",", ".")
    if style == "int":
        return str(int(value))
    return f"{value:,.2f}"


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    write_workbook(out_dir / "synthetic_alpha_elemental.xlsx", "PPM SYNTHETIC ALPHA",
                   "elemental", "15/03/2024", "GFA", "incl_prelims", ALPHA_ROWS, "dots",
                   sector="commercial")
    write_workbook(out_dir / "synthetic_beta_tender.xlsx", "PPM SYNTHETIC BETA",
                   "tender", "2025-01-20", "GFA", "all_in", BETA_ROWS, "int",
                   sector="infrastructure")
    write_workbook(out_dir / "synthetic_gamma_detailed.xlsx", "PPM SYNTHETIC GAMMA",
                   "detailed", "02/11/2024", "NIA", "bare", GAMMA_ROWS, "decimal")
    print(f"wrote 3 synthetic workbooks to {out_dir}")


if __name__ == "__main__":
    main()
