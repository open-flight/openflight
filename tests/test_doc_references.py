"""Doc paths mentioned outside ``docs/`` must exist.

Log messages, docstrings, shell ``warn`` lines, issue templates, and the
top-level guides all point users at ``docs/...md`` pages. The docs
restructure moved those pages into sectioned directories and left a dozen
pointers naming files that no longer exist, including a production WARNING
in the K-LD7 estimator and the setup script every new builder runs. Those
were fixed by hand (PRs #294 and #295); this test fails on any such
reference so a future docs move cannot silently strand them again.

Links *inside* ``docs/`` are not scanned here: the Zensical build runs with
``--strict`` in CI and already fails on broken intra-docs links.
"""

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_REF = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")

# (root, glob) pairs scanned for docs/*.md mentions. Keep this list explicit
# so a new tree that references docs is added deliberately, not by accident.
SCAN_TARGETS: tuple[tuple[str, str], ...] = (
    ("src/openflight", "**/*.py"),
    ("scripts", "**/*.py"),
    ("scripts", "**/*.sh"),
    (".github", "**/*.yml"),
    (".github", "**/*.md"),
    (".", "README.md"),
    (".", "CONTRIBUTING.md"),
    (".", "CLAUDE.md"),
    (".", "AGENTS.md"),
    ("ui", "README.md"),
)


def _exists_with_exact_case(rel_path: str) -> bool:
    """``Path.is_file`` with exact on-disk casing for every component.

    macOS and Windows filesystems are case-insensitive, so ``docs/CHANGELOG.md``
    looks fine there and only breaks on the Linux CI runners (and on GitHub's
    rendered links). Check each component against its parent's real listing.
    """
    current = REPO_ROOT
    for part in Path(rel_path).parts:
        if part not in os.listdir(current):
            return False
        current = current / part
    return current.is_file()


def _doc_references() -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root, pattern in SCAN_TARGETS:
        for path in (REPO_ROOT / root).glob(pattern):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in DOC_REF.findall(text):
                found.add((str(path.relative_to(REPO_ROOT)), match))
    return sorted(found)


_REFERENCES = _doc_references()


def test_scan_finds_references():
    """Guard against the scan silently matching nothing."""
    assert len(_REFERENCES) > 10, f"expected many docs/*.md references, found {len(_REFERENCES)}"


@pytest.mark.parametrize(("source", "doc"), _REFERENCES, ids=[f"{s}:{d}" for s, d in _REFERENCES])
def test_referenced_doc_exists(source, doc):
    assert _exists_with_exact_case(doc), f"{source} points at {doc}, which does not exist"
