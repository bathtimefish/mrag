"""The sync planner against the shared decision table.

``tests/fixtures/sync_transitions_golden.json`` states, for every situation a
sync can meet, what the plan must say. These tests build each case's catalog
and scan facts and check the planner's answer; a rule changes in the table
first, then in the code.
"""

import json
from pathlib import Path

import pytest

from mrag.core.ingestion.rebind_plan import RebindRefused, plan_rebind
from mrag.core.ingestion.sync_plan import (
    ACTIONS,
    REASONS,
    ExternalDirectory,
    ProjectDirectory,
    ScannedSource,
    SourceRootFacts,
    SyncContent,
    SyncDocument,
    SyncExclusion,
    SyncPlanError,
    SyncScope,
    plan_sync,
    resolve_sync_scope,
)

TABLE = json.loads((Path(__file__).parent / "fixtures" / "sync_transitions_golden.json").read_text(encoding="utf-8"))
FOUND = "docs/found.md"


def content(seed: str, converter: str = "native") -> SyncContent:
    return SyncContent(content_hash=f"sha256:{seed}", converter=converter)


def legacy(document_id: str) -> str:
    return f"identities/legacy/v1/{document_id}"


def exclusions(kinds: list[str]) -> list[SyncExclusion]:
    rules = []
    for number, kind in enumerate(kinds):
        if kind == "sync":
            rules.append(SyncExclusion(id=f"sync-{number}", profile_name=None, origin="sync"))
        elif kind == "user_all_profiles":
            rules.append(SyncExclusion(id=f"user-{number}", profile_name=None, origin="user"))
        elif kind == "user_one_profile":
            rules.append(SyncExclusion(id=f"user-{number}", profile_name="default", origin="user"))
        else:
            raise AssertionError(f"unknown exclusion kind {kind}")
    return rules


def scope_of(document_ids: list[str]) -> SyncScope:
    return SyncScope(documents=frozenset(document_ids), unresolvable_roots=0)


def test_the_actions_and_reasons_are_the_shared_ones():
    assert list(TABLE["actions"]) == list(ACTIONS)
    assert list(TABLE["reasons"]) == list(REASONS)


def test_every_shared_found_case_is_decided_as_the_table_says():
    file_content = content("F")
    for case in TABLE["found_cases"]:
        name = case["name"]
        kinds = case["exclusions"]
        documents, scope = [], []
        fact = case["document"]
        if fact in ("same", "different", "same_bytes_other_converter", "never_ready"):
            held = {
                "same": file_content,
                "different": content("G"),
                # The file's bytes, read by a converter this run would not use.
                "same_bytes_other_converter": content("F", "markitdown"),
                "never_ready": None,
            }[fact]
            documents.append(SyncDocument("found", FOUND, held, exclusions(kinds)))
            scope.append("found")
        elif fact != "none":
            raise AssertionError(f"{name}: unknown document fact {fact}")
        match = case["content_match"]
        if match == "missing_document":
            documents.append(SyncDocument("gone", "docs/gone.md", file_content, exclusions(kinds)))
            scope.append("gone")
        elif match == "other_document":
            documents.append(SyncDocument("held", "elsewhere/held.md", file_content))
        elif match == "legacy_document":
            documents.append(SyncDocument("legacy", legacy("legacy"), file_content))
        elif match == "other_and_legacy_document":
            documents.append(SyncDocument("legacy", legacy("legacy"), file_content))
            documents.append(SyncDocument("held", "elsewhere/held.md", file_content))
        elif match != "none":
            raise AssertionError(f"{name}: unknown content_match fact {match}")

        scanned = [ScannedSource(FOUND, file_content, case["conversion_permitted"])]
        plan = plan_sync(documents, scope_of(scope), scanned, [])
        item = next(item for item in plan.items if item.source_identity == FOUND)
        expect = case["expect"]
        assert item.action == expect["action"], name
        assert item.reason == expect["reason"], name
        assert bool(item.revoke_exclusions) == expect["revokes_sync_exclusions"], name
        assert all("sync" in rule for item in plan.items for rule in item.revoke_exclusions), (
            f"{name}: a person's rule was listed for revocation"
        )


def test_every_shared_missing_case_is_decided_as_the_table_says():
    for case in TABLE["missing_cases"]:
        name = case["name"]
        held = {"ready": content("M"), "never_ready": None}[case["document"]]
        gone = SyncDocument("gone", "docs/gone.md", held, exclusions(case["exclusions"]))
        plan = plan_sync([gone], scope_of(["gone"]), [], [])
        assert len(plan.items) == 1, name
        item = plan.items[0]
        expect = case["expect"]
        assert (item.action, item.reason, item.document_id) == (expect["action"], expect["reason"], "gone"), name
        assert item.revoke_exclusions == [], name


def _table_content(entry: dict) -> SyncContent:
    return content(entry["content"], entry.get("converter", "native"))


def test_every_shared_move_case_pairs_exactly_as_the_table_says():
    for case in TABLE["move_cases"]:
        name = case["name"]
        documents, scope = [], []
        for entry in case["missing"]:
            documents.append(SyncDocument(entry["document"], entry["path"], _table_content(entry), exclusions(entry.get("exclusions", []))))
            scope.append(entry["document"])
        scanned = [ScannedSource(entry["path"], _table_content(entry)) for entry in case["appeared"]]
        plan = plan_sync(documents, scope_of(scope), scanned, [])
        expectations = case["expect"]
        assert len(plan.items) == len(expectations), f"{name}: {plan.items}"
        scanned_identities = {source.identity for source in scanned}
        for expect in expectations:
            if "file" in expect:
                item = next(i for i in plan.items if i.source_identity == expect["file"] and i.action != "exclude")
            else:
                item = next(
                    i for i in plan.items
                    if i.previous_identity is None and i.document_id == expect["document"]
                    and i.source_identity not in scanned_identities
                )
            assert item.action == expect["action"], name
            assert item.reason == expect["reason"], name
            if "document" in expect:
                assert item.document_id == expect["document"], name
            assert item.previous_identity == expect.get("previous"), name
            assert bool(item.revoke_exclusions) == expect.get("revokes_sync_exclusions", False), name


def test_a_present_file_outside_the_scan_is_counted_and_never_excluded():
    kept = SyncDocument("kept", "docs/kept.md", content("K"))
    plan = plan_sync([kept], scope_of(["kept"]), [], ["docs/kept.md"])
    assert plan.items == []
    assert plan.out_of_scope == 1


def test_contradictory_observations_are_refused():
    with pytest.raises(SyncPlanError) as repeated:
        plan_sync([], scope_of([]), [ScannedSource("a.md", content("A")), ScannedSource("a.md", content("A"))], [])
    assert repeated.value.code == "sync_scan_identity_repeated"
    with pytest.raises(SyncPlanError) as both:
        plan_sync([], scope_of([]), [ScannedSource("a.md", content("A"))], ["a.md"])
    assert both.value.code == "sync_scan_identity_both_scanned_and_out_of_scope"
    with pytest.raises(SyncPlanError) as unknown:
        plan_sync([], scope_of(["ghost"]), [], [])
    assert unknown.value.code == "sync_scope_document_unknown"


def test_a_project_directory_holds_the_identities_under_its_prefix_and_no_others():
    documents = [
        SyncDocument("a", "docs/a.md", content("A")),
        SyncDocument("b", "docs2/b.md", content("B")),
        SyncDocument("c", "identities/external/0123456789abcdef0123456789abcdef/c.md", content("C")),
    ]
    scope = resolve_sync_scope(ProjectDirectory("docs"), documents, [])
    assert scope.documents == {"a"}
    assert scope.unresolvable_roots == 0
    assert resolve_sync_scope(ProjectDirectory(""), documents, []).documents == {"a", "b"}


def test_an_external_directory_is_resolved_through_its_ancestors_descendants_and_found_roots():
    corpus, reports, elsewhere = "a" * 32, "b" * 32, "c" * 32
    documents = [
        SyncDocument("under-corpus", f"identities/external/{corpus}/reports/x.md", content("X")),
        SyncDocument("under-reports", f"identities/external/{reports}/y.md", content("Y")),
        SyncDocument("elsewhere", f"identities/external/{elsewhere}/z.md", content("Z")),
    ]
    roots = [
        SourceRootFacts(corpus, frozenset()),
        SourceRootFacts(reports, frozenset({corpus})),
        SourceRootFacts(elsewhere, frozenset()),
    ]
    # Syncing `corpus` itself: its own documents by prefix, `reports` by its
    # recorded ancestor. `elsewhere` recorded none and was not found: unresolvable.
    scope = resolve_sync_scope(ExternalDirectory(corpus, {corpus: ""}, frozenset()), documents, roots)
    assert scope.documents == {"under-corpus", "under-reports"}
    assert scope.unresolvable_roots == 1
    # Found by walking, the same root is resolved and no longer unresolvable.
    scope = resolve_sync_scope(ExternalDirectory(corpus, {corpus: ""}, frozenset({elsewhere})), documents, roots)
    assert scope.documents == {"under-corpus", "under-reports", "elsewhere"}
    assert scope.unresolvable_roots == 0
    # Syncing `corpus/reports`: documents under corpus whose path starts with the prefix.
    scope = resolve_sync_scope(ExternalDirectory(reports, {reports: "", corpus: "reports"}, frozenset()), documents, roots)
    assert scope.documents == {"under-corpus", "under-reports"}


def test_rebind_decides_and_refuses_as_the_shared_rules_say():
    documents = [
        SyncDocument("doc", "docs/old.md", content("A"), [SyncExclusion("sync-1", None, "sync")]),
        SyncDocument("other", "docs/other.md", content("B")),
    ]
    moved = plan_rebind(documents, "doc", "docs/new.md", content("A"))
    assert (moved.action, moved.new_revision, moved.revoke_exclusions) == ("rebind", False, ["sync-1"])
    updated = plan_rebind(documents, "doc", "docs/old.md", content("C"))
    assert (updated.action, updated.new_revision) == ("update", True)
    assert plan_rebind(documents, "doc", "docs/old.md", content("A")).action == "restore"
    assert plan_rebind([SyncDocument("doc", "docs/old.md", content("A"))], "doc", "docs/old.md", content("A")).action == "noop"
    assert plan_rebind(documents, "doc", "docs/old.md", content("C"), conversion_permitted=False).action == "blocked"
    for document_id, target, held, code in [
        ("ghost", "docs/x.md", content("A"), "document_not_found"),
        ("doc", "docs/other.md", content("A"), "rebind_target_taken"),
        ("doc", "docs/old.md", content("B"), "rebind_content_held_by_other_document"),
    ]:
        with pytest.raises(RebindRefused) as refused:
            plan_rebind(documents, document_id, target, held)
        assert refused.value.code == code
