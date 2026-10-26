"""Structure-aware chunking without LangChain.

Every chunk keeps a section_ref so an answer can cite the exact sheet range,
page, slide or document heading. Chunks are plain Postgres rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from ppm.loaders import ParsedDocument, ParsedTable

ROW_WINDOW = 20
TEXT_CHUNK_SIZE = 800
TEXT_OVERLAP = 100
MAX_CHUNKS_PER_DOCUMENT = 600


@dataclass(frozen=True)
class ChunkDraft:
    section_ref: str
    text_content: str
    token_estimate: int

    def to_state(self) -> dict:
        return {
            "section_ref": self.section_ref,
            "text_content": self.text_content,
            "token_estimate": self.token_estimate,
        }


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
        text = "\n".join(
            _join_row(row) for row in table.preamble if any(cell not in (None, "") for cell in row)
        )
        if text.strip():
            chunks.append(ChunkDraft(f"{table.name}!header", text, _estimate_tokens(text)))

    header_line = _join_row(table.header) if table.header else None
    for start in range(0, len(table.rows), ROW_WINDOW):
        window = table.rows[start:start + ROW_WINDOW]
        if not window:
            continue
        if not any(cell not in (None, "") for row in window for cell in row):
            continue
        lines: list[str] = [f"Section: {table.name}"]
        if header_line:
            lines.append(header_line)
        for offset, row in enumerate(window):
            lines.append(f"R{table.start_row + start + offset} | {_join_row(row)}")
        text = "\n".join(lines)
        first_row = table.start_row + start
        last_row = first_row + len(window) - 1
        chunks.append(ChunkDraft(
            section_ref=f"{table.name}!R{first_row}-R{last_row}",
            text_content=text,
            token_estimate=_estimate_tokens(text),
        ))
    return chunks


def split_text(text: str, chunk_size: int = TEXT_CHUNK_SIZE, overlap: int = TEXT_OVERLAP) -> list[str]:
    """Split on the largest natural boundary that still fits the window.

    This is a small, dependency-free replacement for RecursiveCharacterTextSplitter.
    It preserves overlap and never returns empty chunks.
    """
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 5)

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            window = text[start:end]
            minimum = max(1, int(chunk_size * 0.55))
            candidates: list[int] = []
            for marker in ("\n\n", "\n", ". ", "! ", "? ", "; ", ", "):
                position = window.rfind(marker)
                if position >= minimum:
                    candidates.append(position + len(marker))
            if window.rfind(" ") >= minimum:
                candidates.append(window.rfind(" ") + 1)
            if candidates:
                end = start + max(candidates)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_document(parsed: ParsedDocument) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for table in parsed.tables:
        drafts.extend(_table_chunks(table))

    for text_block in parsed.texts:
        parts = split_text(text_block.text)
        for index, part in enumerate(parts):
            ref = text_block.name if len(parts) == 1 else f"{text_block.name} (part {index + 1})"
            drafts.append(ChunkDraft(ref, part, _estimate_tokens(part)))

    if len(drafts) > MAX_CHUNKS_PER_DOCUMENT:
        kept = drafts[:MAX_CHUNKS_PER_DOCUMENT]
        kept.append(ChunkDraft(
            section_ref="truncated",
            text_content=(
                f"[Document truncated: {len(drafts) - MAX_CHUNKS_PER_DOCUMENT} further chunks "
                f"were not indexed. Open the source file for the rest.]"
            ),
            token_estimate=30,
        ))
        return kept
    return drafts


def index_document_chunks(document_id: str, parsed: ParsedDocument, settings, *, replace: bool = False) -> tuple[int, str | None]:
    """Chunk + embed + store one document's chunks. Returns (written, warning).

    Embedding failures degrade to NULL embeddings with a warning; the chunks
    still support literal search.
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
