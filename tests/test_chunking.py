from ppm.loaders import ParsedDocument, ParsedTable, ParsedText
from ppm.workflows.chunking import chunk_document


def test_table_chunks_keep_row_refs():
    table = ParsedTable(
        name="Rates",
        start_row=3,
        header=["Code", "Description", "Rate"],
        rows=[[f"{index}", f"Item {index}", index * 1000] for index in range(25)],
    )
    parsed = ParsedDocument(source_path="x.xlsx", mime="x", hash="h", tables=[table])
    chunks = chunk_document(parsed)
    assert len(chunks) == 2
    assert chunks[0].section_ref == "Rates!R3-R22"
    assert chunks[1].section_ref == "Rates!R23-R27"
    assert "R3 | 0 | Item 0 | 0" in chunks[0].text_content


def test_empty_row_windows_are_skipped():
    table = ParsedTable(
        name="Blank",
        start_row=1,
        header=["A", "B"],
        rows=[[None, None] for _ in range(40)],
    )
    parsed = ParsedDocument(source_path="x.xlsx", mime="x", hash="h", tables=[table])
    assert chunk_document(parsed) == []


def test_text_chunks_cite_section():
    parsed = ParsedDocument(
        source_path="x.docx",
        mime="x",
        hash="h",
        texts=[ParsedText(name="Risk management", text="word " * 600)],
    )
    chunks = chunk_document(parsed)
    assert len(chunks) > 1
    assert all(chunk.section_ref.startswith("Risk management") for chunk in chunks)
    assert chunks[0].section_ref.endswith("(part 1)")
