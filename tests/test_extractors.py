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


def test_pymupdf_both_representations_equal(tmp_path: Path):
    from mrag.extractors.pymupdf import PyMuPDFExtractor

    pdf = _make_simple_pdf(tmp_path / "test.pdf")
    result = PyMuPDFExtractor().extract(pdf)
    assert result.text == result.markdown


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
