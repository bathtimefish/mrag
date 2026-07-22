from pathlib import Path

from mrag.extractors.base import BaseExtractor, ExtractionResult

_SCANNED_CHARS_PER_PAGE_THRESHOLD = 100


def _page_to_markdown(page) -> str:
    """Convert a single PyMuPDF page to markdown, rendering tables as pipe syntax.

    Uses find_tables() for layout-based table detection. Text blocks that overlap
    with detected tables are excluded to prevent duplication. Falls back to plain
    get_text("text") when no tables are found or if find_tables() raises.
    """
    import fitz
    try:
        finder = page.find_tables()
        tables = finder.tables
    except Exception:
        return page.get_text("text")

    if not tables:
        return page.get_text("text")

    table_rects = [fitz.Rect(tab.bbox) for tab in tables]
    items: list[tuple[float, str]] = []

    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        blk_rect = fitz.Rect(blk["bbox"])
        if any(blk_rect.intersects(tr) for tr in table_rects):
            continue
        text = "\n".join(
            span["text"]
            for line in blk.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        if text:
            items.append((blk["bbox"][1], text))

    for tab in tables:
        items.append((tab.bbox[1], tab.to_markdown()))

    items.sort(key=lambda x: x[0])
    return "\n\n".join(t for _, t in items).strip()


class PyMuPDFExtractor(BaseExtractor):
    def extract(self, file_path: Path) -> ExtractionResult:
        import fitz

        # PyMuPDF otherwise prints an optional-package recommendation to stdout
        # on the first table analysis, which would corrupt `mrag add --json`.
        no_recommend_layout = getattr(fitz, "no_recommend_layout", None)
        if no_recommend_layout is not None:
            no_recommend_layout()

        doc = fitz.open(str(file_path))
        page_texts: list[str] = []
        page_markdowns: list[str] = []
        for page in doc:
            page_texts.append(page.get_text("text"))
            page_markdowns.append(_page_to_markdown(page))
        page_count = len(doc)
        doc.close()

        text = "\n\n".join(page_texts).strip()
        markdown = "\n\n".join(page_markdowns).strip()

        warnings: list[str] = []
        if page_count > 0 and len(text) / page_count < _SCANNED_CHARS_PER_PAGE_THRESHOLD:
            warnings.append(
                "Extracted text is very short. This PDF may be scanned/image-based."
            )

        return ExtractionResult(
            markdown=markdown,
            text=text,
            warnings=warnings,
            metadata={"page_count": page_count, "char_count": len(text)},
        )
