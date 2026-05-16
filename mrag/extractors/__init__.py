from mrag.extractors.base import BaseExtractor, ExtractionResult
from mrag.extractors.plain import PlainTextExtractor
from mrag.extractors.pymupdf import PyMuPDFExtractor

_SOURCE_TYPE_MAP = {
    ".pdf": "pdf",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".html": "html",
    ".htm": "html",
}


def detect_source_type(file_path) -> str:
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    source_type = _SOURCE_TYPE_MAP.get(suffix)
    if source_type is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    return source_type


def get_extractor(source_type: str, provider: str | None = None) -> BaseExtractor:
    if source_type in ("txt", "md"):
        return PlainTextExtractor()
    if source_type == "pdf":
        provider = provider or "pymupdf"
        if provider == "pymupdf":
            return PyMuPDFExtractor()
        raise ValueError(f"Unsupported PDF extractor: {provider}")
    raise ValueError(f"Unsupported source type: {source_type}")
