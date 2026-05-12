from pathlib import Path

import pytest

from mrag.extractors import detect_source_type, get_extractor
from mrag.extractors.base import ExtractionResult
from mrag.extractors.plain import PlainTextExtractor


# ---------------------------------------------------------------------------
# detect_source_type
# ---------------------------------------------------------------------------

def test_detect_source_type_pdf():
    assert detect_source_type(Path("doc.pdf")) == "pdf"


def test_detect_source_type_txt():
    assert detect_source_type(Path("doc.txt")) == "txt"


def test_detect_source_type_md():
    assert detect_source_type(Path("doc.md")) == "md"
    assert detect_source_type(Path("doc.markdown")) == "md"


def test_detect_source_type_html():
    assert detect_source_type(Path("page.html")) == "html"
    assert detect_source_type(Path("page.htm")) == "html"


def test_detect_source_type_unknown():
    with pytest.raises(ValueError, match="Unsupported file type"):
        detect_source_type(Path("doc.docx"))


# ---------------------------------------------------------------------------
# get_extractor
# ---------------------------------------------------------------------------

def test_get_extractor_txt_returns_plain():
    from mrag.extractors.plain import PlainTextExtractor
    assert isinstance(get_extractor("txt"), PlainTextExtractor)


def test_get_extractor_md_returns_plain():
    from mrag.extractors.plain import PlainTextExtractor
    assert isinstance(get_extractor("md"), PlainTextExtractor)


def test_get_extractor_pdf_default_pymupdf():
    from mrag.extractors.pymupdf import PyMuPDFExtractor
    assert isinstance(get_extractor("pdf"), PyMuPDFExtractor)


def test_get_extractor_pdf_explicit_pymupdf():
    from mrag.extractors.pymupdf import PyMuPDFExtractor
    assert isinstance(get_extractor("pdf", "pymupdf"), PyMuPDFExtractor)


def test_get_extractor_pdf_bad_provider():
    with pytest.raises(ValueError, match="Unsupported PDF extractor"):
        get_extractor("pdf", "unknown_provider")


def test_get_extractor_html_raises():
    with pytest.raises(ValueError, match="Unsupported source type"):
        get_extractor("html")


# ---------------------------------------------------------------------------
# PlainTextExtractor — .txt
# ---------------------------------------------------------------------------

def test_plain_txt_extraction(sample_txt: Path):
    extractor = PlainTextExtractor()
    result = extractor.extract(sample_txt)

    assert isinstance(result, ExtractionResult)
    assert "Hello, world!" in result.text
    assert result.text == result.markdown
    assert result.warnings == []
    assert result.metadata["char_count"] == len(result.text)


def test_plain_txt_both_representations_equal(sample_txt: Path):
    result = PlainTextExtractor().extract(sample_txt)
    assert result.text == result.markdown


# ---------------------------------------------------------------------------
# PlainTextExtractor — .md
# ---------------------------------------------------------------------------

def test_plain_md_extraction(sample_md: Path):
    extractor = PlainTextExtractor()
    result = extractor.extract(sample_md)

    assert "# Test Document" in result.markdown
    assert "# Test Document" in result.text
    assert result.text == result.markdown
    assert result.warnings == []


def test_plain_md_metadata(sample_md: Path):
    result = PlainTextExtractor().extract(sample_md)
    assert result.metadata["char_count"] == len(result.text)


# ---------------------------------------------------------------------------
# PyMuPDFExtractor — tested only when fitz is available
# ---------------------------------------------------------------------------

pytest.importorskip("fitz", reason="pymupdf not installed")


def _make_simple_pdf(path: Path) -> Path:
    """Create a minimal one-page PDF using pymupdf."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from PyMuPDF test.")
    doc.save(str(path))
    doc.close()
    return path


def test_pymupdf_extracts_text(tmp_path: Path):
    from mrag.extractors.pymupdf import PyMuPDFExtractor

    pdf = _make_simple_pdf(tmp_path / "test.pdf")
    result = PyMuPDFExtractor().extract(pdf)

    assert "Hello from PyMuPDF" in result.text
    assert result.text == result.markdown
    assert result.metadata["page_count"] == 1


def test_pymupdf_no_table_pdf_text_equals_markdown(tmp_path: Path):
    """For a plain-text PDF with no tables, text and markdown must be equal
    (both fall back to get_text('text'))."""
    from mrag.extractors.pymupdf import PyMuPDFExtractor

    pdf = _make_simple_pdf(tmp_path / "test.pdf")
    result = PyMuPDFExtractor().extract(pdf)
    assert result.text == result.markdown


# ---------------------------------------------------------------------------
# _page_to_markdown unit tests (mock-based)
# ---------------------------------------------------------------------------

def test_page_to_markdown_no_tables_returns_plain_text():
    """When no tables are found, falls back to get_text('text')."""
    from unittest.mock import MagicMock
    from mrag.extractors.pymupdf import _page_to_markdown

    page = MagicMock()
    finder = MagicMock()
    finder.tables = []
    page.find_tables.return_value = finder
    page.get_text.return_value = "Plain text content"

    result = _page_to_markdown(page)

    assert result == "Plain text content"
    page.get_text.assert_called_once_with("text")


def test_page_to_markdown_find_tables_exception_falls_back():
    """If find_tables() raises, falls back to get_text('text')."""
    from unittest.mock import MagicMock
    from mrag.extractors.pymupdf import _page_to_markdown

    page = MagicMock()
    page.find_tables.side_effect = RuntimeError("layout error")
    page.get_text.return_value = "Fallback text"

    result = _page_to_markdown(page)

    assert result == "Fallback text"


def test_page_to_markdown_with_table_contains_pipe_syntax():
    """When a table is detected, markdown contains pipe-table rows."""
    import fitz
    from unittest.mock import MagicMock
    from mrag.extractors.pymupdf import _page_to_markdown

    mock_table = MagicMock()
    mock_table.bbox = (50.0, 100.0, 400.0, 200.0)
    mock_table.to_markdown.return_value = "| Col A | Col B |\n|---|---|\n| val1 | val2 |"

    mock_finder = MagicMock()
    mock_finder.tables = [mock_table]

    page = MagicMock()
    page.find_tables.return_value = mock_finder
    page.get_text.return_value = {
        "blocks": [
            {
                "type": 0,
                "bbox": [50.0, 10.0, 400.0, 60.0],  # above the table
                "lines": [{"spans": [{"text": "Section heading"}]}],
            }
        ]
    }

    result = _page_to_markdown(page)

    assert "| Col A | Col B |" in result
    assert "val1" in result


def test_page_to_markdown_reading_order():
    """Text blocks above a table appear before the table in the markdown output."""
    import fitz
    from unittest.mock import MagicMock
    from mrag.extractors.pymupdf import _page_to_markdown

    mock_table = MagicMock()
    mock_table.bbox = (50.0, 200.0, 400.0, 300.0)
    mock_table.to_markdown.return_value = "| H1 | H2 |\n|---|---|\n| r1 | r2 |"

    mock_finder = MagicMock()
    mock_finder.tables = [mock_table]

    page = MagicMock()
    page.find_tables.return_value = mock_finder
    page.get_text.return_value = {
        "blocks": [
            {
                "type": 0,
                "bbox": [50.0, 50.0, 400.0, 90.0],  # y=50, above table at y=200
                "lines": [{"spans": [{"text": "Introduction text"}]}],
            },
            {
                "type": 0,
                "bbox": [50.0, 350.0, 400.0, 400.0],  # y=350, below table
                "lines": [{"spans": [{"text": "Footer note"}]}],
            },
        ]
    }

    result = _page_to_markdown(page)

    intro_pos = result.index("Introduction text")
    table_pos = result.index("| H1 |")
    footer_pos = result.index("Footer note")

    assert intro_pos < table_pos < footer_pos


def test_page_to_markdown_overlapping_text_excluded():
    """Text blocks that overlap a table bbox are excluded to avoid duplication."""
    import fitz
    from unittest.mock import MagicMock
    from mrag.extractors.pymupdf import _page_to_markdown

    mock_table = MagicMock()
    mock_table.bbox = (50.0, 100.0, 400.0, 300.0)
    mock_table.to_markdown.return_value = "| A | B |"

    mock_finder = MagicMock()
    mock_finder.tables = [mock_table]

    page = MagicMock()
    page.find_tables.return_value = mock_finder
    page.get_text.return_value = {
        "blocks": [
            {
                "type": 0,
                # Overlaps with table (y=150 is within 100-300)
                "bbox": [60.0, 150.0, 390.0, 250.0],
                "lines": [{"spans": [{"text": "cell text inside table"}]}],
            }
        ]
    }

    result = _page_to_markdown(page)

    assert "cell text inside table" not in result
    assert "| A | B |" in result


def test_pymupdf_scanned_warning(tmp_path: Path):
    """An empty-page PDF triggers the scanned-PDF warning."""
    import fitz
    from mrag.extractors.pymupdf import PyMuPDFExtractor

    doc = fitz.open()
    doc.new_page()  # page with no text inserted
    pdf_path = tmp_path / "empty.pdf"
    doc.save(str(pdf_path))
    doc.close()

    result = PyMuPDFExtractor().extract(pdf_path)
    assert any("scanned" in w.lower() or "short" in w.lower() for w in result.warnings)


def test_pymupdf_no_false_scanned_warning(tmp_path: Path):
    """A PDF with substantial text should NOT trigger the scanned warning."""
    import fitz
    from mrag.extractors.pymupdf import PyMuPDFExtractor

    doc = fitz.open()
    page = doc.new_page()
    # Insert multiple short lines so text is not clipped at page boundary
    text = "This document has enough text content to avoid the scanned-PDF warning.\n" * 3
    page.insert_text((72, 72), text)
    pdf_path = tmp_path / "rich.pdf"
    doc.save(str(pdf_path))
    doc.close()

    result = PyMuPDFExtractor().extract(pdf_path)
    assert result.warnings == []
