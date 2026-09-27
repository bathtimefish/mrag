"""Document-list parity with MRAG Plus through a shared decision table (SPEC-CLI-006).

MRAG Plus holds the canonical fixture (quality/golden/inventory-status.json);
tests/fixtures/inventory_status_golden.json is a byte-identical copy, and both
suites read their own. The facts are product-neutral: this file only turns them
into OSS catalog rows and lets `mrag.core.ingestion.inventory` decide.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from mrag.core.ingestion.inventory import (
    AGGREGATE_STATUSES,
    InventoryQuery,
    _current_profile_hash,
    document_inventory,
    list_envelope,
)
from mrag.db.connection import open_connection

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "inventory_status_golden.json").read_text(encoding="utf-8")
)
PROFILE = FIXTURE["profile"]
SOURCE_STATUS = {"ready": "extracted", "building": "pending", "error": "error"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    import importlib
    from typer.testing import CliRunner
    from mrag.cli import app

    init_mod = importlib.import_module("mrag.cli.init")
    monkeypatch.setattr(init_mod, "detect_best_tokenizer", lambda: ("trigram", None))
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["init", "--name", "kb", "--non-interactive"]).exit_code == 0
    project = tmp_path / "kb"
    # Opening the catalog once records the identity scheme, as any command does.
    open_connection(project / "mrag.db").close()
    from mrag.db.connection import db_connection

    with db_connection(project / "mrag.db"):
        pass
    return project


def _insert(conn, current_hash, document_id, identity, facts):
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, source_identity, filename, original_path, file_hash, "
        "source_type, extracted_hash, status, created_at, updated_at) "
        "VALUES (?, 'kb', ?, ?, 'x', 'h1', 'md', 'e1', ?, 't', 't')",
        (document_id, identity, identity.rsplit("/", 1)[-1], SOURCE_STATUS[facts["source"]]),
    )
    if facts["index"] != "none":
        freshness = facts.get("freshness", "current")
        conn.execute(
            "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, document_file_hash, "
            "extracted_hash, profile_hash, status) VALUES (?, 'kb', ?, ?, ?, 'e1', ?, ?)",
            (
                f"index-{document_id}",
                document_id,
                PROFILE,
                "h0" if freshness == "document_changed" else "h1",
                "an older profile" if freshness == "profile_changed" else current_hash,
                facts["index"],
            ),
        )
    if facts.get("fallback"):
        conn.execute(
            "INSERT INTO chunk_variants (id, knowledge_id, document_id, chunk_id, profile_name, variant_type, "
            "content_for_embedding, metadata_json, created_at) "
            "VALUES (?, 'kb', ?, 'chunk', ?, 'raw', 'text', ?, 't')",
            (f"variant-{document_id}", document_id, PROFILE,
             json.dumps({"embedding_status": "fallback_no_vector"})),
        )
    for kind in facts.get("exclusions", []):
        scope = {"all": None, "this": PROFILE, "other": FIXTURE["other_profile"]}[kind]
        conn.execute(
            "INSERT INTO document_exclusions (id, document_id, profile_name, created_at) VALUES (?, ?, ?, 't')",
            (f"{document_id}:{kind}", document_id, scope),
        )


def _rows(project, **query):
    conn = open_connection(project / "mrag.db")
    try:
        return document_inventory(conn, project, PROFILE, **query)
    finally:
        conn.close()


def test_every_shared_status_case_is_decided_as_the_table_says(project):
    current_hash = _current_profile_hash(project, PROFILE)
    cases = FIXTURE["status_cases"]
    with sqlite3.connect(project / "mrag.db") as conn:
        for number, case in enumerate(cases):
            _insert(conn, current_hash, f"case-{number}", f"cases/{number}.md", case)
    rows = {row["document_id"]: row for row in _rows(project, include_all=True)["rows"]}

    for number, case in enumerate(cases):
        row, expect = rows[f"case-{number}"], case["expect"]
        exclusion = expect["exclusion"]
        assert (
            row["source_status"], row["index_status"], row["retrieval_status"],
            row["aggregate_status"], row["exclusion_id"],
        ) == (
            expect["source_status"], expect["index_status"], expect["retrieval_status"],
            expect["aggregate"], f"case-{number}:{exclusion}" if exclusion else None,
        ), case["name"]
        # The intentional difference: the row's `status` is the stored value.
        assert row["status"] == SOURCE_STATUS[case["source"]], case["name"]


def test_every_shared_listing_case_filters_counts_and_orders_as_the_table_says(project):
    listing = FIXTURE["listing_cases"]
    current_hash = _current_profile_hash(project, PROFILE)
    with sqlite3.connect(project / "mrag.db") as conn:
        for document in listing["documents"]:
            _insert(conn, current_hash, document["id"], document["identity"], document)

    for query in listing["queries"]:
        expect = query["expect"]
        inventory = _rows(project, include_all=query["all"], statuses=set(query["statuses"]))
        envelope = list_envelope(
            inventory, InventoryQuery(include_all=query["all"], statuses=set(query["statuses"]))
        )
        assert envelope["filter"]["statuses"] == expect["filter_statuses"], query
        assert (envelope["total"], envelope["returned"]) == (expect["total"], expect["returned"]), query
        assert [row["document_id"] for row in inventory["rows"]] == expect["order"], query


@pytest.mark.parametrize("case", FIXTURE["page_cases"], ids=lambda case: json.dumps(case))
def test_every_shared_page_case_pages_as_the_table_says(case):
    inventory = {"profile": PROFILE, "total": case["rows"],
                 "rows": [{"document_id": f"doc-{n}"} for n in range(case["rows"])]}
    page = list_envelope(inventory, InventoryQuery(limit=case["limit"], offset=case["offset"]))["page"]
    assert (page["count"], page["next_offset"]) == (case["expect"]["count"], case["expect"]["next_offset"])


def test_the_fields_and_the_priority_are_the_shared_ones(project):
    assert list(AGGREGATE_STATUSES) == FIXTURE["aggregate_priority"]

    current_hash = _current_profile_hash(project, PROFILE)
    with sqlite3.connect(project / "mrag.db") as conn:
        _insert(conn, current_hash, "doc-1", "docs/a.md", {"source": "ready", "index": "none"})
    inventory = _rows(project)
    [row] = inventory["rows"]

    differences = FIXTURE["oss_differences"]
    expected = list(FIXTURE["row_fields"])
    expected.insert(expected.index(differences["aggregate_field_follows"]) + 1, differences["aggregate_field"])
    expected += differences["appended_fields"]
    assert list(row) == expected
    assert row["ingest_ms"] == differences["ingest_ms"]

    envelope = list_envelope(inventory, InventoryQuery())
    assert list(envelope) == FIXTURE["envelope_fields"]
    assert list(envelope["filter"]) == FIXTURE["filter_fields"]
    assert list(envelope["page"]) == FIXTURE["page_fields"]
