"""exp/html-structure: an HTML document from `mrag add` to a search result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

pytest.importorskip("html5lib")

from mrag.api.app import create_app  # noqa: E402
from mrag.cli import app as cli_app  # noqa: E402
from mrag.config.project import load_project_config  # noqa: E402
from mrag.core.indexing.pipeline import run_index  # noqa: E402
from mrag.db.connection import find_db, open_connection  # noqa: E402
from mrag.extractors import detect_source_type  # noqa: E402
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client  # noqa: E402

runner = CliRunner()

# A bed-care sheet, written for this test: a heading, a page break, a table
# whose rows are read under a header row and a row header, two criterion
# values, and a sentence after the table.
BEDS = """<!doctype html><html><body>
<h1>Bed care</h1>
<span role="doc-pagebreak" aria-label="8"></span>
<h2>Watering</h2>
<table><caption>Spacing of cabbages</caption>
<thead><tr><th>Crop</th><th>Gap</th></tr></thead>
<tbody>
<tr><th>Cabbages</th><td><span data-criterion data-limit="range" data-unit="cm">40 to 50</span></td></tr>
<tr><th>Kale</th><td><span data-criterion data-limit="max" data-unit="cm">60</span></td></tr>
</tbody></table>
<p>Water cabbages at dusk.</p>
</body></html>
"""


def _project(tmp_path: Path, monkeypatch, *, choose_html: bool) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["init", "--name", "kb", "--non-interactive"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    project = tmp_path / "kb"
    if choose_html:
        config = project / "mrag.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "conversion:\n  converter:\n    html: html-structure\n",
            encoding="utf-8",
        )
    monkeypatch.chdir(project)
    return project


def test_html_is_refused_unless_the_project_chooses_html_structure(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, choose_html=False)
    page = project / "beds.html"
    page.write_text(BEDS, encoding="utf-8")
    with pytest.raises(ValueError, match="requires external conversion"):
        detect_source_type(page, load_project_config(project))
    result = runner.invoke(cli_app, ["add", str(page)])
    assert result.exit_code != 0


def test_an_html_document_is_searched_with_its_rows_criteria_and_pages(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, choose_html=True)
    page = project / "beds.html"
    page.write_text(BEDS, encoding="utf-8")
    notes = project / "notes.md"
    notes.write_text("# Notes\n\nWater the beds at dawn.\n", encoding="utf-8")
    for source in (page, notes):
        result = runner.invoke(cli_app, ["add", str(source)], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    db_path = find_db(project)
    conn = open_connection(db_path)
    row = conn.execute(
        "SELECT source_type, extraction_provider, extraction_output_format, extracted_markdown_path "
        "FROM documents WHERE filename = 'beds.html'"
    ).fetchone()
    conn.close()
    assert (row["source_type"], row["extraction_provider"], row["extraction_output_format"]) == (
        "html", "html-structure", "markdown",
    )
    record = json.loads((project / row["extracted_markdown_path"]).parent.joinpath("structure.json").read_text(encoding="utf-8"))
    assert [table["caption"] for table in record["tables"]] == ["Spacing of cabbages"]

    config = load_project_config(project)
    provider, qdrant = FakeEmbeddingProvider(), _fake_qdrant_client()
    run_index(project_dir=project, config=config, profile_name="default",
              embedding_provider=provider, qdrant_client=qdrant)

    application = create_app(project_dir=project, profile_name="default", config=config,
                             _embedding_provider=provider, _qdrant_client=qdrant)
    with TestClient(application) as client:
        body = client.post("/api/v1/retrieve", json={"query": "Water", "strategy": "keyword", "top_k": 5}).json()
    by_file = {result["filename"]: result for result in body["results"]}
    assert "beds.html" in by_file and "notes.md" in by_file, [(r["filename"], r["content"][:30]) for r in body["results"]]
    beds, plain = by_file["beds.html"], by_file["notes.md"]

    # The whole sheet is one chunk: its header row and the rule under it
    # are dropped, each body row is read under its headers.
    assert beds["structured_content"] == (
        "# Bed care\n\n## Watering\n\nSpacing of cabbages\n\n"
        "・Cabbages: 「Gap」= 40 to 50\n・Kale: 「Gap」= 60\n\nWater cabbages at dusk."
    )
    assert [(c["value"], c["limit"], c["unit"], c["row_labels"], c["column_labels"], c["heading"], c["page_label"])
            for c in beds["criteria"]] == [
        ("40 to 50", "range", "cm", ["Cabbages"], ["Gap"], "Watering", "8"),
        ("60", "max", "cm", ["Kale"], ["Gap"], "Watering", "8"),
    ]
    assert beds["criteria_omitted"] == 0
    assert beds["reference"]["page_label_end"] == "8"
    assert "html_structure" not in beds["metadata"]
    # A document without a structure record carries the fields, empty.
    assert (plain["structured_content"], plain["criteria"], plain["criteria_omitted"]) == (None, [], 0)
    assert plain["reference"]["page_label_start"] is None
