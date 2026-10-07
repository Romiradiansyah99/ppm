"""Structure-aware chunking (plan section 9: build with langchain-text-splitters,
tuned per document type). Every chunk keeps a section_ref back to the source so
answers can cite the exact sheet range or page - plain rows, no framework
schema."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ppm.loaders import ParsedDocument, ParsedTable

ROW_WINDOW = 20          # spreadsheet rows per chunk
TEXT_CHUNK_SIZE = 800    # characters per text chunk (pdf pages, notes)
TEXT_OVERLAP = 100


@dataclass(frozen=True)
class ChunkDraft:
    section_ref: str
    text_content: str
    token_estimate: int


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _join_row(row: list[object]) -> str:
    cells = [" " if cell is None else str(cell).strip() for cell in row]
    while cells and not cells[-1].strip():
        cells.pop()
    return " | ".join(cells)


def _table_chunks(table: ParsedTable) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    if table.preamble:
        text = "\n".join(_join_row(row) for row in table.preamble if any(cell not in (None, "") for cell in row))
        if text.strip():
            chunks.append(ChunkDraft(f"{table.name}!header", text, _estimate_tokens(text)))

    header_line = _join_row(table.header) if table.header else None
    for start in range(0, len(table.rows), ROW_WINDOW):
        window = table.rows[start:start + ROW_WINDOW]
        if not window:
            continue
        lines = []
        if header_line:
            lines.append(header_line)
        for offset, row in enumerate(window):
            lines.append(f"R{table.start_row + start + offset} | {_join_row(row)}")
        text = "\n".join(lines)
        first_row = table.start_row + start
        last_row = first_row + len(window) - 1
        ref = f"{table.name}!R{first_row}-R{last_row}"
        chunks.append(ChunkDraft(ref, text, _estimate_tokens(text)))
    return chunks


def chunk_document(parsed: ParsedDocument) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for table in parsed.tables:
        drafts.extend(_table_chunks(table))

    splitter = RecursiveCharacterTextSplitter(chunk_size=TEXT_CHUNK_SIZE, chunk_overlap=TEXT_OVERLAP)
    for text_block in parsed.texts:
        parts = splitter.split_text(text_block.text)
        for index, part in enumerate(parts):
            ref = text_block.name if len(parts) == 1 else f"{text_block.name} (part {index + 1})"
            drafts.append(ChunkDraft(ref, part, _estimate_tokens(part)))
    return drafts


def index_document_chunks(document_id: str, parsed: ParsedDocument, settings, *, replace: bool = False) -> tuple[int, str | None]:
    """Chunk + embed + store one document's chunks. Returns (written, warning).

    Shared by the ingest graph and `ppm reindex`; embedding failures degrade to
    NULL embeddings (filters/literal search still works) with a warning.
    """
    from ppm import db
    from ppm.models import ModelUnavailable, embed_texts

    drafts = chunk_document(parsed)
    vectors: list[list[float] | None] = [None] * len(drafts)
    warning: str | None = None
    try:
        vectors = list(embed_texts(settings, [draft.text_content for draft in drafts]))
    except ModelUnavailable as exc:
        warning = f"chunk embeddings skipped: {exc}"

    with db.connection() as conn, conn.cursor() as cur:
        if replace:
            cur.execute("DELETE FROM document_chunk WHERE document_id = %s", (document_id,))
        for index, (draft, vector) in enumerate(zip(drafts, vectors)):
            cur.execute(
                "INSERT INTO document_chunk (document_id, chunk_index, section_ref, text_content, "
                "token_estimate, embedding) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (document_id, chunk_index) DO NOTHING",
                (document_id, index, draft.section_ref, draft.text_content, draft.token_estimate, vector),
            )
    return len(drafts), warning
