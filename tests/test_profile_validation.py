"""Tests for ProfileConfig cross-field validation (parent_child consistency)."""
import pytest
from pydantic import ValidationError

from mrag.config.profile import ProfileConfig, validate_effective_tokenizer


class TestParentChildValidation:
    def test_both_parent_child_valid(self):
        prof = ProfileConfig(
            name="pc",
            chunking={"strategy": "parent_child"},
            retrieval={"strategy": "parent_child"},
        )
        assert prof.chunking.strategy == "parent_child"
        assert prof.retrieval.strategy == "parent_child"

    def test_chunking_parent_child_retrieval_hybrid_raises(self):
        with pytest.raises(ValidationError, match="retrieval.strategy"):
            ProfileConfig(
                name="bad",
                chunking={"strategy": "parent_child"},
                retrieval={"strategy": "hybrid"},
            )

    def test_retrieval_parent_child_chunking_recursive_raises(self):
        with pytest.raises(ValidationError, match="chunking.strategy"):
            ProfileConfig(
                name="bad",
                chunking={"strategy": "recursive"},
                retrieval={"strategy": "parent_child"},
            )

    def test_default_profile_is_valid(self):
        prof = ProfileConfig(name="default")
        assert prof.chunking.strategy != "parent_child"
        assert prof.retrieval.strategy != "parent_child"

    def test_both_hybrid_valid(self):
        prof = ProfileConfig(
            name="h",
            chunking={"strategy": "recursive"},
            retrieval={"strategy": "hybrid"},
        )
        assert prof is not None

    def test_parent_config_defaults(self):
        prof = ProfileConfig(name="pc", chunking={"strategy": "parent_child"}, retrieval={"strategy": "parent_child"})
        assert prof.chunking.parent.max_chars == 3000
        assert prof.chunking.parent.strategy == "fixed_size"

    def test_child_config_defaults(self):
        prof = ProfileConfig(name="pc", chunking={"strategy": "parent_child"}, retrieval={"strategy": "parent_child"})
        assert prof.chunking.child.chunk_size == 600
        assert prof.chunking.child.overlap == 100

    def test_child_size_equal_to_parent_raises(self):
        with pytest.raises(ValidationError, match="must be smaller"):
            ProfileConfig(
                name="bad",
                chunking={
                    "strategy": "parent_child",
                    "parent": {"max_chars": 3000},
                    "child": {"chunk_size": 3000},
                },
                retrieval={"strategy": "parent_child"},
            )

    def test_child_size_larger_than_parent_raises(self):
        with pytest.raises(ValidationError, match="must be smaller"):
            ProfileConfig(
                name="bad",
                chunking={
                    "strategy": "parent_child",
                    "parent": {"max_chars": 500},
                    "child": {"chunk_size": 600},
                },
                retrieval={"strategy": "parent_child"},
            )

    def test_child_size_relationship_ignored_for_non_parent_child(self):
        # Default config has child.chunk_size=600 and parent.max_chars=3000 (irrelevant for recursive).
        # If we deliberately put child > parent on a non-parent_child profile, validation should pass.
        prof = ProfileConfig(
            name="ok",
            chunking={
                "strategy": "recursive",
                "parent": {"max_chars": 500},
                "child": {"chunk_size": 600},
            },
        )
        assert prof.chunking.strategy == "recursive"


class TestRetrievalFusionValidation:
    def test_default_fusion_is_rrf(self):
        prof = ProfileConfig(name="d")
        assert prof.retrieval.fusion == "rrf"
        assert prof.retrieval.weights is None

    def test_weighted_fusion_accepted(self):
        prof = ProfileConfig(name="w", retrieval={"fusion": "weighted", "weights": [0.7, 0.3]})
        assert prof.retrieval.fusion == "weighted"
        assert prof.retrieval.weights == [0.7, 0.3]

    def test_invalid_fusion_value_raises(self):
        with pytest.raises(ValidationError):
            ProfileConfig(name="bad", retrieval={"fusion": "harmonic_mean"})

    def test_weights_wrong_length_raises(self):
        with pytest.raises(ValidationError, match="exactly 2 elements"):
            ProfileConfig(name="bad", retrieval={"fusion": "weighted", "weights": [0.5]})
        with pytest.raises(ValidationError, match="exactly 2 elements"):
            ProfileConfig(name="bad", retrieval={"fusion": "weighted", "weights": [0.3, 0.3, 0.4]})

    def test_weights_nonpositive_sum_raises(self):
        with pytest.raises(ValidationError, match="positive value"):
            ProfileConfig(name="bad", retrieval={"fusion": "weighted", "weights": [0.0, 0.0]})

    @pytest.mark.parametrize("weights", [[-0.1, 1.1], [float("nan"), 1.0], [float("inf"), 1.0]])
    def test_weights_negative_or_nonfinite_raise(self, weights):
        with pytest.raises(ValidationError, match="finite and non-negative"):
            ProfileConfig(name="bad", retrieval={"fusion": "weighted", "weights": weights})

    @pytest.mark.parametrize(
        "retrieval",
        [
            {"top_k": 0},
            {"strategy": "hybrid", "top_k": 8, "dense_top_k": 7},
            {"strategy": "hybrid", "top_k": 8, "keyword_top_k": 7},
        ],
    )
    def test_candidate_limits_are_validated(self, retrieval):
        with pytest.raises(ValidationError):
            ProfileConfig(name="bad", retrieval=retrieval)

    def test_weights_excluded_from_profile_hash(self):
        """Changing weights should not invalidate the index (retrieval-time only)."""
        a = ProfileConfig(name="a", retrieval={"fusion": "weighted", "weights": [0.5, 0.5]})
        b = ProfileConfig(name="a", retrieval={"fusion": "weighted", "weights": [0.9, 0.1]})
        assert a.compute_hash() == b.compute_hash()


class TestIndexIdentity:
    def test_all_query_time_retrieval_fields_are_excluded(self):
        baseline = ProfileConfig(name="same")
        tuned = ProfileConfig(
            name="same",
            retrieval={
                "strategy": "hybrid",
                "top_k": 12,
                "dense_top_k": 60,
                "keyword_top_k": 70,
                "fusion": "weighted",
                "weights": [0.8, 0.2],
            },
        )

        assert baseline.compute_hash() == tuned.compute_hash()
        assert baseline.compute_config_hash() != tuned.compute_config_hash()

    def test_embedding_cache_is_excluded_but_endpoint_remains_conservative(self):
        baseline = ProfileConfig(name="same")
        cached = ProfileConfig(name="same", embedding={"cache": {"enabled": True}})
        remote = ProfileConfig(
            name="same",
            embedding={"endpoint": "http://other-host:11434"},
        )

        assert baseline.compute_hash() == cached.compute_hash()
        assert baseline.compute_hash() != remote.compute_hash()

    def test_context_prompt_changes_identity_only_when_contextual(self):
        raw = ProfileConfig(name="raw")
        assert raw.compute_hash(context_prompt="prompt-a") == raw.compute_hash(
            context_prompt="prompt-b"
        )

        contextual = ProfileConfig(
            name="contextual",
            augmentation={"strategy": "contextual"},
        )
        assert contextual.compute_hash(
            context_prompt="prompt-a {document} {chunk}"
        ) != contextual.compute_hash(
            context_prompt="prompt-b {document} {chunk}"
        )

    def test_unused_augmentation_runtime_values_are_excluded(self):
        baseline = ProfileConfig(name="raw")
        unused = ProfileConfig(
            name="raw",
            augmentation={
                "strategy": "none",
                "model": "unused-model",
                "endpoint": "http://unused-host:11434",
            },
        )

        assert baseline.compute_hash() == unused.compute_hash()

    def test_effective_tokenizer_participates_in_identity(self):
        profile = ProfileConfig(name="same")

        assert profile.compute_hash(effective_tokenizer="trigram") != profile.compute_hash(
            effective_tokenizer="vaporetto"
        )

    def test_project_and_profile_tokenizer_must_match(self):
        profile = ProfileConfig(name="same", keyword={"tokenizer": "vaporetto"})

        with pytest.raises(ValueError, match="Tokenizer mismatch"):
            validate_effective_tokenizer(profile, "trigram")

        assert validate_effective_tokenizer(profile, "vaporetto") == "vaporetto"


class TestRetiredExtractionConfig:
    """mrag 1.0 removed in-process PDF extraction along with its config keys.

    Projects created before that still carry `extraction:` in profiles and
    `default_extraction:` in mrag.yaml. Loading must keep working, and the keys
    must not disturb the index identity, or every existing knowledge base would
    be forced through a full reindex on upgrade.
    """

    _LEGACY_EXTRACTION = {"pdf": {"provider": "pymupdf", "output_format": "markdown"}}

    def test_legacy_profile_extraction_key_is_ignored(self):
        profile = ProfileConfig(name="legacy", extraction=self._LEGACY_EXTRACTION)

        assert not hasattr(profile, "extraction")

    def test_legacy_profile_extraction_key_does_not_change_index_identity(self):
        legacy = ProfileConfig(name="same", extraction=self._LEGACY_EXTRACTION)
        current = ProfileConfig(name="same")

        assert legacy.compute_hash() == current.compute_hash()

    def test_legacy_project_default_extraction_key_is_ignored(self, tmp_path):
        import yaml

        from mrag.config.project import load_project_config

        (tmp_path / "mrag.yaml").write_text(
            yaml.safe_dump(
                {
                    "project": {"name": "legacy"},
                    "knowledge_base": {"id": "kb-legacy", "name": "Legacy"},
                    "default_profile": "default",
                    "fts_tokenizer": "trigram",
                    "default_extraction": self._LEGACY_EXTRACTION,
                    "qdrant": {"mode": "local"},
                }
            ),
            encoding="utf-8",
        )

        config = load_project_config(tmp_path)

        assert config.knowledge_id == "kb-legacy"
        assert not hasattr(config, "default_extraction")
