"""Structure-aware chunking: section refs, windowing, provenance."""

from __future__ import annotations

from openpyxl import Workbook

from ppm.loaders import load_document
from ppm.workflows.chunking import ROW_WINDOW, chunk_document


def test_table_chunks_have_section_refs(samples):
    parsed = load_document(samples["alpha"])
    chunks = chunk_document(parsed)
    refs = [chunk.section_ref for chunk in chunks]

    assert "Cost Plan!header" in refs                    # preamble chunk
    assert "Cost Plan!R13-R26" in refs                   # 14 data rows, one window
    assert any(ref.startswith("Basis!") for ref in refs)
    assert all(chunk.text_content.strip() for chunk in chunks)
    assert all(chunk.token_estimate >= 1 for chunk in chunks)

    header_chunk = next(chunk for chunk in chunks if chunk.section_ref == "Cost Plan!header")
    assert "PPM SYNTHETIC ALPHA" in header_chunk.text_content


def test_large_sheet_windows(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rates"
    sheet.append(["Description", "Unit", "Rate"])
    for index in range(2 * ROW_WINDOW + 5):
        sheet.append([f"Item {index}", "m2", 1000 + index])
    path = tmp_path / "big.xlsx"
    workbook.save(path)

    chunks = chunk_document(load_document(path))
    refs = [chunk.section_ref for chunk in chunks]
    assert refs == [
        f"Rates!R2-R{1 + ROW_WINDOW}",
        f"Rates!R{2 + ROW_WINDOW}-R{1 + 2 * ROW_WINDOW}",
        f"Rates!R{2 + 2 * ROW_WINDOW}-R{2 + 2 * ROW_WINDOW + 4}",
    ]


def test_pdf_chunks_by_page(tmp_path):
    from tests.test_loaders import _minimal_pdf

    path = tmp_path / "sample.pdf"
    path.write_bytes(_minimal_pdf("PPM SAMPLE PDF CONTENT"))
    chunks = chunk_document(load_document(path))
    assert len(chunks) == 1
    assert chunks[0].section_ref == "page 1"
    assert "PPM SAMPLE PDF" in chunks[0].text_content
