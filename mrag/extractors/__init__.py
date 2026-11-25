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


_HTML_SUFFIXES = {".html", ".htm"}


def detect_source_type(file_path, config=None) -> str:
    """The source type of a file. ``.html``/``.htm`` are ``html`` when the
    project ``config`` chooses ``conversion.converter.html: html-structure``
    (experimental branch exp/html-structure), and refused otherwise."""
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    source_type = _SOURCE_TYPE_MAP.get(suffix)
    if source_type is None and suffix in _HTML_SUFFIXES and config is not None and config.converts_html:
        return "html"
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
    if source_type == "html":
        try:
            import html5lib  # noqa: F401
        except ImportError as error:
            raise ValueError(
                "html-structure needs the html5lib parser: install it with pip install 'mrag[html]'"
            ) from error
        from mrag.extractors.html_structure import HtmlStructureExtractor

        return HtmlStructureExtractor()
    raise ValueError(f"Unsupported source type: {source_type}")
