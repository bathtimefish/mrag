from pathlib import Path

from mrag.extractors.base import BaseExtractor, ExtractionResult


class PlainTextExtractor(BaseExtractor):
    """Extractor for .txt and .md files — reads content directly."""

    def extract(self, file_path: Path) -> ExtractionResult:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        suffix = file_path.suffix.lower()

        if suffix == ".md":
            # Markdown file: markdown = raw content, text = same (readable as-is)
            return ExtractionResult(
                markdown=content,
                text=content,
                metadata={"char_count": len(content)},
            )

        # .txt: both representations are the same plain text
        return ExtractionResult(
            markdown=content,
            text=content,
            metadata={"char_count": len(content)},
        )
