"""Tests for ProfileConfig cross-field validation (parent_child consistency)."""
import pytest
from pydantic import ValidationError

from mrag.config.profile import ProfileConfig


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

    def test_weights_excluded_from_profile_hash(self):
        """Changing weights should not invalidate the index (retrieval-time only)."""
        a = ProfileConfig(name="a", retrieval={"fusion": "weighted", "weights": [0.5, 0.5]})
        b = ProfileConfig(name="a", retrieval={"fusion": "weighted", "weights": [0.9, 0.1]})
        assert a.compute_hash() == b.compute_hash()
