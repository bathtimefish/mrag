"""The version is written in four places; they move together (1.7.6).

`SKILL.md` said 1.2.0 for five releases because nothing compared it with the
package. A release bumps all four, and this test is what notices one left
behind.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import mrag

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_fallback_version_matches_the_package():
    assert mrag._FALLBACK_VERSION == _pyproject_version()


def test_lockfile_records_the_package_version():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    mrag_entries = [package for package in lock["package"] if package["name"] == "mrag"]
    assert [entry["version"] for entry in mrag_entries] == [_pyproject_version()]


def test_skill_metadata_names_the_package_version():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    found = re.search(r'^\s*version:\s*"?([^"\s]+)"?\s*$', front_matter, re.MULTILINE)
    assert found, "SKILL.md front matter has metadata.version"
    assert found.group(1) == _pyproject_version()
