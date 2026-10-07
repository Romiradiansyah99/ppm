"""Loaders: workbook parsing, header detection, provenance row refs, and the
PDF page-text path against a hand-built minimal PDF."""

from __future__ import annotations

import pytest

from ppm.loaders import UnsupportedFormatError, load_document
from ppm.loaders.pdf import parse_pdf


def test_alpha_workbook_parses_with_provenance(samples):
    parsed = load_document(samples["alpha"])
    assert len(parsed.hash) == 64
    assert parsed.mime.endswith("spreadsheetml.sheet")
    assert [t.name for t in parsed.tables] == ["Cost Plan", "Basis"]

    sheet = parsed.tables[0]
    assert sheet.header is not None
    assert "Description" in sheet.header
    assert sheet.start_row == 13          # 10 metadata rows + blank + header, then data
    assert sheet.row_ref(0) == "Cost Plan!R13"
    assert len(sheet.rows) == 14


def test_unsupported_format_rejected(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError):
        load_document(path)


def test_workbook_hash_is_stable(samples):
    assert load_document(samples["alpha"]).hash == load_document(samples["alpha"]).hash


def _minimal_pdf(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        None,  # content stream, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects[3] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


def test_pdf_page_text_parses(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(_minimal_pdf("PPM SAMPLE PDF"))
    tables, texts = parse_pdf(path)
    assert not tables
    assert len(texts) == 1
    assert texts[0].name == "page 1"
    assert "PPM SAMPLE PDF" in texts[0].text


def test_pdf_loader_through_facade(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(_minimal_pdf("PPM SAMPLE PDF"))
    parsed = load_document(path)
    assert parsed.mime == "application/pdf"
    assert parsed.texts and parsed.texts[0].name == "page 1"
