"""Every search result carries a `reference`: where its text came from.

The same object on `mrag search --json`, the native API and the MCP `search`
tool, naming each document exactly as the document list does, and the stored
original behind it served by `mrag://documents/<id>/original`.
"""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mrag.api.app import create_app
from mrag.cli import app
from mrag.config.mcp import load_mcp_config, resolve_mcp_config
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.core.ingestion.inventory import document_inventory
from mrag.db.connection import open_connection
from mrag.mcp.resources import original_resource
from mrag.mcp.tools import McpToolContext, search_tool
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client
from tests.test_source_identity import _project, _to_scheme_one

runner = CliRunner()

REFERENCE_KEYS = {
    "display_name",
    "source_binding_status",
    "source_path",
    "original",
    "extracted_markdown",
    "resource",
    "revision_id",
    "location",
}


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch):
    """One document inside the project and one under an external root, indexed."""
    project, add = _project(tmp_path, monkeypatch)
    (project / "manuals").mkdir()
    (project / "manuals" / "inside.md").write_text(
        "# Inside\n\nThe alpha procedure as the team wrote it.\n", encoding="utf-8"
    )
    outside = tmp_path / "corpus"
    outside.mkdir()
    (outside / "notes.md").write_text(
        "# Notes\n\nThe alpha procedure as a supplier sent it.\n", encoding="utf-8"
    )
    add(project / "manuals" / "inside.md")
    add(outside / "notes.md")
    provider = FakeEmbeddingProvider()
    qdrant = _fake_qdrant_client()
    run_index(
        project_dir=project,
        config=load_project_config(project),
        profile_name="default",
        embedding_provider=provider,
        qdrant_client=qdrant,
    )
    return project, provider, qdrant


def _search_json(*extra: str) -> dict:
    result = runner.invoke(
        app, ["search", "alpha", "--strategy", "keyword", "--json", *extra], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _by_name(payload: dict) -> dict[str, dict]:
    return {entry["reference"]["display_name"]: entry for entry in payload["results"]}


def _chunk_format(project: Path, chunk_id: str) -> str:
    conn = open_connection(project / "mrag.db")
    try:
        return conn.execute("SELECT source_format FROM chunks WHERE id = ?", (chunk_id,)).fetchone()[0]
    finally:
        conn.close()


def test_each_result_names_its_source_original_and_extraction(corpus):
    project, _, _ = corpus
    results = _by_name(_search_json())
    assert set(results) == {"manuals/inside.md", "corpus/notes.md"}, results

    inside = results["manuals/inside.md"]
    reference = inside["reference"]
    assert set(reference) == REFERENCE_KEYS
    document_id = inside["document_id"]
    assert reference["source_binding_status"] == "project_relative"
    assert reference["source_path"] == "manuals/inside.md"
    assert reference["original"] == f"data/documents/{document_id}/original.md"
    assert reference["extracted_markdown"] == f"data/documents/{document_id}/extracted.md"
    assert reference["resource"] == f"mrag://documents/{document_id}/original"
    # A document keeps one extraction, so there is no revision to name, and
    # chunks record no character range.
    assert reference["revision_id"] is None
    assert reference["location"] == {
        "source_format": _chunk_format(project, inside["chunk_id"]),
        "source_start": None,
        "source_end": None,
    }
    for path in (reference["original"], reference["extracted_markdown"]):
        assert not Path(path).is_absolute()
        assert (project / path).is_file()

    outside = results["corpus/notes.md"]["reference"]
    assert outside["source_binding_status"] == "external_root"
    # The catalog does not record where an external root is on disk.
    assert outside["source_path"] is None
    assert (project / outside["original"]).is_file()


def test_the_human_output_names_the_source_and_the_original(corpus):
    result = runner.invoke(app, ["search", "alpha", "--strategy", "keyword"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    payload = _search_json()
    assert "source: manuals/inside.md" in result.stdout
    for entry in payload["results"]:
        assert entry["reference"]["original"] in result.stdout


def test_the_api_and_the_mcp_tool_return_the_same_reference(corpus):
    project, provider, qdrant = corpus
    expected = {entry["chunk_id"]: entry["reference"] for entry in _search_json()["results"]}

    fastapi_app = create_app(
        project_dir=project,
        profile_name="default",
        config=load_project_config(project),
        _embedding_provider=provider,
        _qdrant_client=qdrant,
    )
    with TestClient(fastapi_app) as client:
        for path in ("/api/v1/retrieve", "/api/v1/search"):
            response = client.post(path, json={"query": "alpha", "strategy": "keyword"})
            assert response.status_code == 200, response.text
            served = {entry["chunk_id"]: entry["reference"] for entry in response.json()["results"]}
            assert served == expected, path
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        assert set(schema["SearchReference"]["required"]) == REFERENCE_KEYS

    ctx = McpToolContext(resolve_mcp_config(load_mcp_config(env={"MRAG_PROJECT_DIR": str(project)}), env={}))
    tool = search_tool(ctx, query="alpha", strategy="keyword")
    assert {entry["chunk_id"]: entry["reference"] for entry in tool["results"]} == expected


def test_a_reference_names_a_document_as_the_document_list_does(corpus):
    """Read through one function, so the two cannot disagree — including on a
    row stored before source identities and on a catalog nobody has migrated."""
    project, _, _ = corpus
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("UPDATE documents SET source_identity = NULL WHERE filename = 'inside.md'")
    _to_scheme_one(project)

    conn = open_connection(project / "mrag.db")
    try:
        listed = {
            row["document_id"]: row
            for row in document_inventory(conn, project, "default", include_all=True)["rows"]
        }
    finally:
        conn.close()
    results = _search_json()["results"]
    assert results
    for entry in results:
        reference, row = entry["reference"], listed[entry["document_id"]]
        assert reference["display_name"] == row["display_name"]
        assert reference["source_binding_status"] == row["source_binding_status"]
    legacy = next(e["reference"] for e in results if e["reference"]["source_binding_status"] == "legacy_unbound")
    assert legacy["source_path"] is None


def _mcp_context(project: Path, **limits) -> McpToolContext:
    config = load_mcp_config(env={"MRAG_PROJECT_DIR": str(project)})
    for key, value in limits.items():
        setattr(config.limits, key, value)
    return McpToolContext(resolve_mcp_config(config, env={}))


def _read_original(project: Path, document_id: str):
    from mrag.mcp.server import build_fastmcp

    ctx = _mcp_context(project)
    server = build_fastmcp(ctx.effective)
    (contents,) = asyncio.run(server.read_resource(f"mrag://documents/{document_id}/original"))
    return contents


def test_the_original_resource_serves_a_text_original_as_its_own_type(corpus):
    project, _, _ = corpus
    entry = _by_name(_search_json())["manuals/inside.md"]

    contents = _read_original(project, entry["document_id"])

    assert contents.mime_type == "text/markdown"
    assert contents.content == (project / entry["reference"]["original"]).read_text(encoding="utf-8")


def test_a_binary_original_is_served_as_a_blob_of_its_recorded_type(corpus):
    """A PDF added before 1.0.0 keeps `source_type='pdf'`; the type decides
    text or blob, not whether the bytes happen to decode."""
    project, _, _ = corpus
    entry = _by_name(_search_json())["corpus/notes.md"]
    original = project / entry["reference"]["original"]
    original.write_bytes(b"%PDF-1.4\n\xff\xfe scanned\n%%EOF\n")
    with sqlite3.connect(project / "mrag.db") as conn:
        conn.execute("UPDATE documents SET source_type = 'pdf' WHERE id = ?", (entry["document_id"],))

    contents = _read_original(project, entry["document_id"])

    assert contents.mime_type == "application/pdf"
    assert contents.content == original.read_bytes()


def test_an_original_over_the_byte_limit_is_refused_not_cut(corpus):
    project, _, _ = corpus
    entry = _by_name(_search_json())["manuals/inside.md"]

    with pytest.raises(ValueError, match="artifact_max_bytes"):
        original_resource(_mcp_context(project, artifact_max_bytes=8), entry["document_id"])


def test_the_byte_limit_defaults_to_one_mebibyte():
    assert load_mcp_config(env={}).limits.artifact_max_bytes == 1048576
