from pathlib import Path

from mrag.extractors.base import BaseExtractor, ExtractionResult

_SCANNED_CHARS_PER_PAGE_THRESHOLD = 100


class PyMuPDFExtractor(BaseExtractor):
    def extract(self, file_path: Path) -> ExtractionResult:
        import fitz

        doc = fitz.open(str(file_path))
        page_texts = [page.get_text("text") for page in doc]
        text = "\n\n".join(page_texts).strip()
        page_count = len(doc)
        doc.close()

        warnings: list[str] = []
        if page_count > 0 and len(text) / page_count < _SCANNED_CHARS_PER_PAGE_THRESHOLD:
            warnings.append(
                "Extracted text is very short. This PDF may be scanned/image-based."
            )

        # Both files contain the same plain text — per spec:
        # "pymupdf + output_format:text → extracted.txt = 抽出テキスト / extracted.md = 同内容"
        return ExtractionResult(
            markdown=text,
            text=text,
            warnings=warnings,
            metadata={"page_count": page_count, "char_count": len(text)},
        )
