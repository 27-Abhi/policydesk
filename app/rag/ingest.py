import os
from pathlib import Path
from typing import Optional

from app.rag.store import ParentChildVectorStore


def load_pdf_text(pdf_path: str | Path) -> str:
    """Extract readable text from a PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    path_obj = Path(pdf_path)
    if not path_obj.exists():
        return ""

    reader = PdfReader(str(path_obj))
    pages_text = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt and txt.strip():
            pages_text.append(txt.strip())

    return "\n\n".join(pages_text)


def ingest_pdf_file(
    store: ParentChildVectorStore,
    pdf_path: str | Path,
    doc_id: Optional[str] = None,
    title: Optional[str] = None,
) -> int:
    """Ingest a single PDF document into the parent-child vector store.

    Returns the number of parent chunks added.
    """
    path_obj = Path(pdf_path)
    if not path_obj.exists():
        return 0

    text = load_pdf_text(path_obj)
    if not text.strip():
        return 0

    assigned_id = doc_id or path_obj.stem.lower()
    assigned_title = title or path_obj.stem.replace("_", " ").title()

    initial_parents = len(store.parents)
    store.add_document(doc_id=assigned_id, title=assigned_title, text=text)
    return len(store.parents) - initial_parents


def ingest_policies_directory(
    store: ParentChildVectorStore,
    dir_path: str | Path,
) -> int:
    """Ingest all readable PDF files in a directory into the store."""
    directory = Path(dir_path)
    if not directory.exists() or not directory.is_dir():
        return 0

    total_parents_added = 0
    for pdf_file in directory.glob("*.pdf"):
        added = ingest_pdf_file(store, pdf_file)
        total_parents_added += added

    return total_parents_added
