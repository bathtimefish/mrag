from pathlib import Path

import pytest

from mrag.extractors import detect_source_type, get_extractor
from mrag.extractors.base import ExtractionResult
from mrag.extractors.plain import PlainTextExtractor


# ---------------------------------------------------------------------------
# detect_source_type
# ---------------------------------------------------------------------------

def test_detect_source_type_txt():
    assert detect_source_type(Path("doc.txt")) == "txt"


def test_detect_source_type_md():
    assert detect_source_type(Path("doc.md")) == "md"
    assert detect_source_type(Path("doc.markdown")) == "md"


def test_detect_source_type_unknown():
    with pytest.raises(ValueError, match="Unsupported file type"):
        detect_source_type(Path("doc.bin"))


@pytest.mark.parametrize(
    "name",
    ["doc.pdf", "page.html", "page.htm", "doc.docx", "deck.pptx", "book.xlsx"],
)
def test_detect_source_type_conversion_required(name: str):
    """Formats a conversion engine owns are rejected with their own message."""
    with pytest.raises(ValueError, match="requires external conversion to Markdown"):
        detect_source_type(Path(name))


def test_detect_source_type_unknown_has_no_conversion_hint():
    """An unknown extension must not claim a conversion engine would help."""
    with pytest.raises(ValueError) as excinfo:
        detect_source_type(Path("archive.zip"))
    assert "requires external conversion" not in str(excinfo.value)


def test_detect_source_type_is_case_insensitive():
    assert detect_source_type(Path("DOC.MD")) == "md"
    with pytest.raises(ValueError, match="requires external conversion to Markdown"):
        detect_source_type(Path("DOC.PDF"))


# ---------------------------------------------------------------------------
# get_extractor
# ---------------------------------------------------------------------------

def test_get_extractor_txt_returns_plain():
    assert isinstance(get_extractor("txt"), PlainTextExtractor)


def test_get_extractor_md_returns_plain():
    assert isinstance(get_extractor("md"), PlainTextExtractor)


@pytest.mark.parametrize("source_type", ["pdf", "html", "docx"])
def test_get_extractor_rejects_unsupported_source_type(source_type: str):
    with pytest.raises(ValueError, match="Unsupported source type"):
        get_extractor(source_type)


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
