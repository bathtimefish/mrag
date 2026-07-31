from mrag.extractors.base import BaseExtractor, ExtractionResult
from mrag.extractors.plain import PlainTextExtractor

_SOURCE_TYPE_MAP = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
}

# Formats mrag deliberately does not read in-process. They carry structure that
# only a document conversion engine recovers, so they are rejected with their own
# message instead of being lumped in with genuinely unknown extensions.
_CONVERSION_REQUIRED = {
    ".pdf",
    ".html",
    ".htm",
    ".docx",
    ".pptx",
    ".xlsx",
}


def detect_source_type(file_path) -> str:
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    source_type = _SOURCE_TYPE_MAP.get(suffix)
    if source_type is None:
        if suffix in _CONVERSION_REQUIRED:
            raise ValueError(
                f"Unsupported file type: {suffix} (requires external conversion to Markdown)"
            )
        raise ValueError(f"Unsupported file type: {suffix}")
    return source_type


def get_extractor(source_type: str) -> BaseExtractor:
    if source_type in ("txt", "md"):
        return PlainTextExtractor()
    raise ValueError(f"Unsupported source type: {source_type}")
