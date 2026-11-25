"""What the sdist must carry beyond what setuptools infers.

setuptools builds an sdist from what is referenced: ``readme`` brings the README, the package finder brings the
package. The CHANGELOG and the pages under ``docs/`` are referenced by nothing, so only ``MANIFEST.in`` ships them.
Each rule is on the declaration; nothing is built.
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_the_changelog_is_shipped_in_the_sdist() -> None:
    manifest = _REPO / "MANIFEST.in"
    assert manifest.is_file() and (_REPO / "CHANGELOG.md").is_file()
    included = [
        line.split(maxsplit=1)[1].strip() for line in manifest.read_text().splitlines() if line[:8] == "include "
    ]
    assert "CHANGELOG.md" in included


def test_every_docs_page_the_readme_links_is_shipped_in_the_sdist() -> None:
    """The README links the pages under ``docs/`` on GitHub; the sdist carries them too, so a reader who has only the
    sdist still has them. `MANIFEST.in` must cover every linked page."""
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    linked = sorted(set(re.findall(r"/blob/main/(docs/[\w./-]+\.md)", readme)))
    assert linked and all((_REPO / page).is_file() for page in linked)
    rules = [line.split() for line in (_REPO / "MANIFEST.in").read_text().splitlines() if line and line[0] != "#"]
    shipped = {str(path.relative_to(_REPO)) for rule in rules if rule[0] == "include" for path in _REPO.glob(rule[1])}
    for rule in rules:
        if rule[0] == "recursive-include":
            shipped |= {str(p.relative_to(_REPO)) for pattern in rule[2:] for p in (_REPO / rule[1]).rglob(pattern)}
    assert [page for page in linked if page not in shipped] == []
