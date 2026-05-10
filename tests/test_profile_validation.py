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
