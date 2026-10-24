"""The task prefixes, as the embedding model receives them.

`embedding.query_prefix` goes before a search query and nothing else;
`embedding.document_prefix` goes before every chunk an index run embeds, with
`{title}` filled from the document's file name. Neither reaches keyword search
or the stored variants. These tests record what the provider is asked to embed
rather than reading the code paths that build it.
"""
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.prefix import document_input, document_title, query_input
from mrag.core.indexing.pipeline import run_index
from mrag.core.retrieval.runner import run_retrieval
from mrag.db.connection import find_db, open_connection

runner = CliRunner()

QUERY_PREFIX = "task: search result | query: "
TITLED = "title: {title} | text: "
PROBE = " "


class RecordingProvider(BaseEmbeddingProvider):
    """Embeds every text as the same unit vector and keeps what it was given."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self._dimension = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

    def get_dimension(self) -> int:
        return self._dimension

    def get_model_id(self) -> str:
        return "fake:recording:4:v1"

    def get_normalized_name(self) -> str:
        return "recording"

    def ensure_model_registered(self, db_path: Path) -> None:
        from mrag.db.connection import db_connection

        with db_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO embedding_models "
                "(id, provider, model_name, dimension, model_revision, normalized_name, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.get_model_id(), "fake", "recording", 4, "v1", "recording", "2026-01-01T00:00:00+00:00"),
            )

    def chunk_inputs(self) -> list[str]:
        return [text for text in self.texts if text != PROBE]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(app, ["init", "--name", "kb", "--non-interactive"], catch_exceptions=False)
    assert created.exit_code == 0, created.output
    project_dir = tmp_path / "kb"
    monkeypatch.chdir(project_dir)
    (project_dir / "corpus").mkdir()
    (project_dir / "corpus" / "alpha.md").write_text(
        "# Alpha\n\nThe quick brown fox jumps over the lazy dog.\n", encoding="utf-8"
    )
    added = runner.invoke(app, ["add", "corpus/alpha.md"], catch_exceptions=False)
    assert added.exit_code == 0, added.output
    return project_dir


def _edit_embedding(project_dir: Path, profile: str = "default", **fields) -> None:
    path = project_dir / "profiles" / f"{profile}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            data["embedding"].pop(key, None)
        else:
            data["embedding"][key] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _index(project_dir: Path, provider: RecordingProvider, profile: str = "default"):
    return run_index(
        project_dir=project_dir,
        config=load_project_config(project_dir),
        profile_name=profile,
        embedding_provider=provider,
        qdrant_client=MagicMock(),
    )


def _rows(project_dir: Path, sql: str) -> list[tuple]:
    conn = open_connection(find_db(project_dir))
    try:
        return [tuple(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _qdrant_answering_with(project_dir: Path) -> MagicMock:
    [(chunk_id, document_id)] = _rows(project_dir, "SELECT id, document_id FROM chunks")
    hit = MagicMock()
    hit.id = str(uuid.uuid4())
    hit.score = 0.9
    hit.payload = {"chunk_id": chunk_id, "document_id": document_id, "profile_name": "default"}
    qdrant = MagicMock()
    qdrant.query_points.return_value.points = [hit]
    return qdrant


# ---------------------------------------------------------------------------
# The rules themselves
# ---------------------------------------------------------------------------


def test_without_a_prefix_a_text_is_embedded_exactly_as_written():
    assert query_input("fox", None) == "fox"
    assert document_input("The fox.", None, "alpha") == "The fox."


def test_a_prefix_goes_before_the_text_and_the_title_fills_its_placeholder_once():
    assert query_input("fox", QUERY_PREFIX) == "task: search result | query: fox"
    assert document_input("The fox.", TITLED, "alpha") == "title: alpha | text: The fox."
    assert document_input("{title}", "{title}: ", "a") == "a: {title}"


@pytest.mark.parametrize(
    ("filename", "title"),
    [
        ("alpha.md", "alpha"),
        ("release.notes.txt", "release.notes"),
        ("README", "README"),
        (" spaced .md", "spaced"),
        ("ｶﾀｶﾅ.md", "カタカナ"),
        (".md", "none"),
        ("", "none"),
        (None, "none"),
    ],
)
def test_the_title_is_the_file_name_without_its_extension_or_none(filename, title):
    assert document_title(filename) == title


# ---------------------------------------------------------------------------
# What a new project sends
# ---------------------------------------------------------------------------


def test_a_new_project_prefixes_the_query_and_embeds_its_chunks_as_written(project: Path):
    provider = RecordingProvider()
    _index(project, provider)

    assert provider.chunk_inputs()
    assert not any(text.startswith("task:") for text in provider.chunk_inputs())

    qdrant = _qdrant_answering_with(project)
    config = load_project_config(project)
    for strategy in ("vector", "hybrid"):
        provider.texts.clear()
        run = run_retrieval(
            query="lazy fox", project_dir=project, config=config, strategy=strategy,
            embedding_provider=provider, qdrant_client=qdrant,
        )
        assert provider.texts == ["task: search result | query: lazy fox"], strategy
        assert run.results, strategy


def test_keyword_search_is_given_the_query_as_written(project: Path):
    _index(project, RecordingProvider())
    seen: list[str] = []

    from mrag.core.retrieval import hybrid

    original = hybrid.keyword_search

    def recording_keyword_search(**kwargs):
        seen.append(kwargs["query_text"])
        return original(**kwargs)

    with patch.object(hybrid, "keyword_search", recording_keyword_search):
        run_retrieval(
            query="lazy fox", project_dir=project, config=load_project_config(project),
            strategy="hybrid", embedding_provider=RecordingProvider(),
            qdrant_client=_qdrant_answering_with(project),
        )

    assert seen == ["lazy fox"]


def test_parent_child_retrieval_prefixes_the_query(project: Path):
    (project / "profiles" / "pc.yaml").write_text(
        yaml.safe_dump({
            "name": "pc",
            "chunking": {"strategy": "parent_child", "source_format": "markdown"},
            "retrieval": {"strategy": "parent_child"},
            "embedding": {"model": "embeddinggemma-2:740m-q8_0", "query_prefix": QUERY_PREFIX},
        }),
        encoding="utf-8",
    )
    provider = RecordingProvider()
    _index(project, provider, profile="pc")
    provider.texts.clear()

    run_retrieval(
        query="lazy fox", project_dir=project, config=load_project_config(project),
        profile_name="pc", embedding_provider=provider, qdrant_client=MagicMock(),
    )

    assert provider.texts == ["task: search result | query: lazy fox"]


def test_eval_embeds_the_query_as_search_does(project: Path):
    _index(project, RecordingProvider())
    provider = RecordingProvider()

    with patch("mrag.cli.eval.OllamaEmbeddingProvider", return_value=provider), \
            patch("mrag.cli.eval.make_client", return_value=_qdrant_answering_with(project)):
        result = runner.invoke(app, ["eval", "lazy fox", "--strategy", "vector"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert provider.texts == ["task: search result | query: lazy fox"]


def test_changing_the_query_prefix_rebuilds_nothing(project: Path):
    _index(project, RecordingProvider())
    _edit_embedding(project, query_prefix="query: ")

    provider = RecordingProvider()
    result = _index(project, provider)

    assert (result.indexed, result.skipped) == (0, 1)
    assert provider.chunk_inputs() == []


# ---------------------------------------------------------------------------
# The document prefix
# ---------------------------------------------------------------------------


def test_a_titled_document_prefix_reaches_every_chunk_and_nothing_stored(project: Path):
    _edit_embedding(project, document_prefix=TITLED)
    provider = RecordingProvider()
    _index(project, provider)

    inputs = provider.chunk_inputs()
    assert inputs and all(text.startswith("title: alpha | text: ") for text in inputs)
    stored = [row[0] for row in _rows(project, "SELECT content_for_embedding FROM chunk_variants")]
    assert stored == [text.removeprefix("title: alpha | text: ") for text in inputs]
    assert not any("title:" in row[0] for row in _rows(project, "SELECT content FROM chunks"))


def test_setting_the_document_prefix_rebuilds_the_index_past_the_cache(project: Path):
    _edit_embedding(project, cache={"enabled": True})
    _index(project, RecordingProvider())

    _edit_embedding(project, document_prefix="passage: ")
    provider = RecordingProvider()
    result = _index(project, provider)

    assert result.indexed == 1
    inputs = provider.chunk_inputs()
    assert inputs and all(text.startswith("passage: ") for text in inputs)


def test_a_renamed_document_is_embedded_again_under_its_new_title(project: Path):
    _edit_embedding(project, document_prefix=TITLED)
    _index(project, RecordingProvider())

    (project / "corpus" / "alpha.md").rename(project / "corpus" / "beta.md")
    synced = runner.invoke(app, ["documents", "sync", "corpus", "--apply"], catch_exceptions=False)
    assert synced.exit_code == 0, synced.output
    provider = RecordingProvider()
    result = _index(project, provider)

    assert result.indexed == 1
    inputs = provider.chunk_inputs()
    assert inputs and all(text.startswith("title: beta | text: ") for text in inputs)


# ---------------------------------------------------------------------------
# Every query path builds its provider from the whole profile
# ---------------------------------------------------------------------------


def test_the_api_and_eval_send_the_profiles_input_limit_as_search_does(project: Path):
    from fastapi.testclient import TestClient

    from mrag.api.app import create_app

    _index(project, RecordingProvider())
    config = load_project_config(project)
    built: dict[str, dict] = {}

    def recorder(surface: str):
        def build(**kwargs):
            built[surface] = kwargs
            return RecordingProvider()
        return build

    with patch("mrag.core.retrieval.runner.OllamaEmbeddingProvider", side_effect=recorder("search")), \
            patch("mrag.core.retrieval.runner.make_client", return_value=MagicMock()):
        run_retrieval(query="fox", project_dir=project, config=config, strategy="vector")
    with patch("mrag.api.app.OllamaEmbeddingProvider", side_effect=recorder("api")):
        with TestClient(create_app(project_dir=project, profile_name="default", config=config,
                                   _qdrant_client=MagicMock())):
            pass
    with patch("mrag.cli.eval.OllamaEmbeddingProvider", side_effect=recorder("eval")), \
            patch("mrag.cli.eval.make_client", return_value=MagicMock()):
        assert runner.invoke(app, ["eval", "fox", "--strategy", "vector"]).exit_code == 0

    assert built["search"]["max_input_tokens"] == 8192
    assert built["api"] == built["search"] == built["eval"]
    assert set(built["search"]) == {
        "model", "endpoint", "max_attempts", "initial_delay", "backoff_multiplier", "max_delay", "max_input_tokens",
    }
