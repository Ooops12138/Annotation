"""Runtime paths resolved independently of the process working directory."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOKS_DIR = PROJECT_ROOT / "books"
STORAGE_DIR = PROJECT_ROOT / "storage"
SOURCE_INDEX_PATH = STORAGE_DIR / "source-index.sqlite3"


def first_book_pdf() -> Path:
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"books/ 中没有教材 PDF: {BOOKS_DIR}")
    return pdfs[0]
