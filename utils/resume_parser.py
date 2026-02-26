"""
Resume text extraction — Parses PDF resumes for profile analysis.
Caches extracted text to avoid re-parsing every time.
"""

import hashlib
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def extract_resume_text(resume_path: str) -> str:
    """
    Extract text from a PDF resume.
    Returns cached text if the file hasn't changed.
    """
    path = Path(resume_path)
    if not path.exists():
        return ""

    # Cache key based on file content hash
    file_hash = hashlib.md5(path.read_bytes()).hexdigest()
    cache_file = CACHE_DIR / f"resume_{file_hash}.txt"

    if cache_file.exists():
        return cache_file.read_text()

    try:
        import pdfplumber
    except ImportError:
        print("  ⚠ pdfplumber not installed. Run: pip install pdfplumber")
        return ""

    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"  ⚠ Failed to parse resume: {e}")
        return ""

    # Cache the result
    cache_file.write_text(text)
    return text
