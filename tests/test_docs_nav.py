"""Every docs page must be reachable from the Zensical site navigation.

Zensical builds every Markdown file under ``docs/`` whether or not
``zensical.toml`` lists it, and ``--strict`` does not warn about pages left
out of ``nav``. Such a page exists at a URL but has no sidebar or tab entry,
so a reader can only find it through an occasional inline link. The OV9281
camera guide, the OPS243 internal hardware-trigger guide, the IWR6843 vs
K-LD7 comparison, and the OPS243 vendor-docs index all ended up that way.

This test fails when a page is added to ``docs/`` without a nav entry, or
when nav names a page that no longer exists.
"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
CONFIG = REPO_ROOT / "zensical.toml"


def _flatten_nav(entries) -> list[str]:
    """Collect every page path from a nested Zensical ``nav`` value.

    ``nav`` entries are a bare path, a ``{title: path}`` table, or a
    ``{title: [entries...]}`` table for a section.
    """
    pages: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            pages.append(entry)
        elif isinstance(entry, dict):
            for value in entry.values():
                if isinstance(value, str):
                    pages.append(value)
                elif isinstance(value, list):
                    pages.extend(_flatten_nav(value))
                else:
                    raise TypeError(f"unexpected nav value: {value!r}")
        else:
            raise TypeError(f"unexpected nav entry: {entry!r}")
    return pages


def _nav_pages() -> list[str]:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    return _flatten_nav(config["project"]["nav"])


def _docs_pages() -> list[str]:
    return sorted(path.relative_to(DOCS_DIR).as_posix() for path in DOCS_DIR.rglob("*.md"))


_NAV = _nav_pages()
_DOCS = _docs_pages()


def test_nav_is_not_empty():
    """Guard against the flattener silently matching nothing."""
    assert len(_NAV) > 50, f"expected many nav pages, found {len(_NAV)}"


def test_nav_has_no_duplicates():
    duplicates = sorted({page for page in _NAV if _NAV.count(page) > 1})
    assert not duplicates, f"pages listed more than once in nav: {duplicates}"


@pytest.mark.parametrize("page", _DOCS)
def test_docs_page_is_in_nav(page):
    assert page in _NAV, (
        f"docs/{page} is built but not in zensical.toml nav, so readers cannot reach it"
    )


@pytest.mark.parametrize("page", _NAV)
def test_nav_page_exists(page):
    assert (DOCS_DIR / page).is_file(), f"zensical.toml nav lists docs/{page}, which does not exist"


def test_camera_yolo_links_to_camera_guide():
    """The YOLO experiment page is where readers look for camera setup."""
    text = (DOCS_DIR / "development" / "camera-yolo.md").read_text(encoding="utf-8")
    assert "(../camera/README.md" in text
