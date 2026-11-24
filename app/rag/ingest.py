"""Structure-aware policy ingestion and PDF text sanitization utilities."""

import logging
import re
from pathlib import Path
from typing import Optional

from app.rag.store import ParentChildVectorStore

logger = logging.getLogger("policydesk.rag.ingest")

_PDF_HEADER_FOOTER_PATTERNS = (
    re.compile(r"^\s*Comprehensive\s+Deposit\s+Policy(?:\s+[A-Za-z]+\s+\d{4})?\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Page\s+\d+(?:\s+of\s+\d+)?\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\d{1,3}\s*$", re.MULTILINE),
)

_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n)\s*((?:\d{1,2}\.\s+[A-Z][A-Z0-9\s,&/\-()]{3,80})|(?:\([ivx]+\)\s+[A-Z][A-Za-z0-9\s,&/\-()]{3,80}))"
)


def clean_pdf_page_text(text: str) -> str:
    """Remove running PDF page numbers, document banners, and fix broken line-wrap spacing."""
    cleaned = text
    for pattern in _PDF_HEADER_FOOTER_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    # Collapse excessive spaces/blank lines within a page while keeping paragraph structure
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_section_title(text: str, fallback: str = "General Policy") -> str:
    """Extract the leading section heading from a chunk if present."""
    match = _SECTION_HEADING_RE.search(text)
    if match:
        heading = re.sub(r"\s+", " ", match.group(1)).strip()
        return heading[:90]
    return fallback


def load_pdf_pages(pdf_path: str | Path) -> list[tuple[int, str]]:
    """Extract cleaned (page_number, text) tuples from a PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    path_obj = Path(pdf_path)
    if not path_obj.exists():
        return []

    reader = PdfReader(str(path_obj))
    pages: list[tuple[int, str]] = []
    for page_num, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = clean_pdf_page_text(raw)
        if cleaned:
            pages.append((page_num, cleaned))

    if not pages:
        logger.warning("[RAG:INGEST] PDF '%s' yielded 0 extractable characters (scanned/image PDF skipped).", path_obj.name)
    return pages


def load_pdf_text(pdf_path: str | Path) -> str:
    """Extract cleaned readable text from a PDF file."""
    pages = load_pdf_pages(pdf_path)
    return "\n\n".join(text for _, text in pages)


def sanitize_upload_filename(filename: str) -> str:
    """Strip directory traversal components and normalize policy filenames."""
    base = Path(filename or "uploaded_policy.txt").name
    safe = re.sub(r"[^a-zA-Z0-9._\-]", "_", base)
    return safe or "uploaded_policy.txt"


def ingest_pdf_file(
    store: ParentChildVectorStore,
    pdf_path: str | Path,
    doc_id: Optional[str] = None,
    title: Optional[str] = None,
) -> int:
    """Ingest a single PDF document with page and section metadata into the vector store."""
    path_obj = Path(pdf_path)
    if not path_obj.exists():
        return 0

    pages = load_pdf_pages(path_obj)
    if not pages:
        return 0

    assigned_id = doc_id or path_obj.stem.lower()
    assigned_title = title or path_obj.stem.replace("_", " ").title()

    initial_parents = len(store.parents)
    page_texts = [text for _, text in pages]
    page_numbers = [num for num, _ in pages]
    full_text = "\n\n".join(page_texts)

    store.add_document(
        doc_id=assigned_id,
        title=assigned_title,
        text=full_text,
        page_spans=[(num, txt) for num, txt in zip(page_numbers, page_texts)],
    )
    return len(store.parents) - initial_parents


def ingest_policies_directory(
    store: ParentChildVectorStore,
    dir_path: str | Path,
) -> int:
    """Ingest all readable PDF, TXT, and Markdown policy files in a directory."""
    directory = Path(dir_path)
    if not directory.exists() or not directory.is_dir():
        return 0

    total_parents_added = 0
    for pdf_file in sorted(directory.glob("*.pdf")):
        added = ingest_pdf_file(store, pdf_file)
        total_parents_added += added

    for txt_file in sorted(list(directory.glob("*.txt")) + list(directory.glob("*.md"))):
        assigned_id = txt_file.stem.lower()
        assigned_title = txt_file.stem.replace("_", " ").title()
        if any(p.get("doc_id") == assigned_id for p in store.parents.values()):
            continue
        try:
            content = txt_file.read_text(encoding="utf-8")
            if content.strip():
                initial = len(store.parents)
                store.add_document(doc_id=assigned_id, title=assigned_title, text=content)
                total_parents_added += len(store.parents) - initial
        except Exception as exc:
            logger.warning("[RAG:INGEST] Failed reading text policy '%s': %s", txt_file.name, exc)

    return total_parents_added
