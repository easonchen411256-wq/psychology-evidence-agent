"""Safe local text extraction for supported paper input formats."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}


class PaperInputError(RuntimeError):
    """Raised when local paper material cannot be read safely."""


def extract_pdf_text(path: Path) -> str:
    """Extract searchable text from a local PDF; reject encrypted or image-only files."""
    try:
        reader = PdfReader(str(path))
    except Exception as error:
        raise PaperInputError("PDF could not be opened. Use a readable local PDF file.") from error
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as error:
            raise PaperInputError(
                "PDF is password protected. Use an accessible copy before analysis."
            ) from error
        if not unlocked:
            raise PaperInputError(
                "PDF is password protected. Use an accessible copy before analysis."
            )
    pages: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise PaperInputError(
                f"Text could not be extracted from PDF page {page_number}."
            ) from error
        if text.strip():
            pages.append(f"【第 {page_number} 页】\n{text.strip()}")
    result = "\n\n".join(pages).strip()
    if not result:
        raise PaperInputError(
            "No searchable text was found in this PDF. Use OCR or provide a .md/.txt transcription."
        )
    return result


def read_paper_text(path: Path, *, max_chars: int, allow_large_input: bool = False) -> str:
    """Read UTF-8 text or extract local PDF text, enforcing the shared size limit."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise PaperInputError("Supported input formats are .md, .txt, and .pdf.")
    try:
        text = extract_pdf_text(path) if suffix == ".pdf" else path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PaperInputError(f"Input file was not found: {path}") from error
    except UnicodeDecodeError as error:
        raise PaperInputError("Text input is not UTF-8. Convert it before analysis.") from error
    if not text.strip():
        raise PaperInputError("Paper material is empty.")
    if len(text) > max_chars and not allow_large_input:
        raise PaperInputError(
            f"Paper material exceeds {max_chars:,} characters. Shorten it or add --allow-large-input."
        )
    return text
