"""Doc paths mentioned from the Python package must exist.

Log messages, docstrings, and comments under ``src/openflight`` point users
at ``docs/...md`` pages. The docs restructure moved those pages into
sectioned directories and several pointers were left naming files that no
longer exist (see the kld7 OPS-bin penalty warning, which sent users to a
404 for months). This test fails on any such reference so a future docs
move cannot silently strand them again.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "src" / "openflight"
DOC_REF = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")


def _doc_references() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in sorted(set(DOC_REF.findall(text))):
            found.append((str(path.relative_to(REPO_ROOT)), match))
    return found


_REFERENCES = _doc_references()


def test_package_mentions_at_least_one_doc():
    """Guard against the scan silently matching nothing."""
    assert _REFERENCES, "expected src/openflight to reference docs/*.md somewhere"


@pytest.mark.parametrize(("source", "doc"), _REFERENCES, ids=[f"{s}:{d}" for s, d in _REFERENCES])
def test_referenced_doc_exists(source, doc):
    assert (REPO_ROOT / doc).is_file(), f"{source} points at {doc}, which does not exist"
