"""Where a config's names point — companion to ``docs/editor.md``.

Writes a small project in a temp directory and asks :func:`confluid.locate.locate` where each class, config file and
import of its config points: the dotted classes are found in their source (nothing imported), the includes where
``load()`` reads them, a ``config:`` path where a run started from the project folder reads it, a ``pkg://`` value
under any key as the file its distribution ships, and a registry name comes back deferred — the editor extension
resolves it on click.
"""

import tempfile
from pathlib import Path
from typing import Optional

from confluid.locate import Location, locate

CONFIG = """\
include:
  - base.yaml
  - missing.yaml
import: [confluid.loader]
reader: !class:confluid.loader.load
plain: {_target_: confluid.dump}
trainer: !partial:Trainer
steps:
  - config: config/train.yaml
  - config: pkg://not-installed/a.yaml
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        (project / "config").mkdir(parents=True)
        (project / "pyproject.toml").write_text("")
        (project / "config" / "base.yaml").write_text("seed: 1\n")
        (project / "config" / "train.yaml").write_text("seed: 2\n")
        config = project / "config" / "experiment.yaml"

        mentions = locate(CONFIG, config)  # the editor's unsaved text works the same: the file need not exist
        lines = CONFIG.splitlines()
        for m in mentions:
            where = (
                f"{Path(m.target.path).name}:{m.target.line + 1}" if m.target else ("deferred" if m.deferred else "—")
            )
            print(f"{m.kind:8} line {m.line + 1}  {lines[m.line][m.start:m.stop]:28} -> {where}")

        assert [m.text for m in mentions] == [
            "base.yaml",
            "missing.yaml",
            "confluid.loader",
            "confluid.loader.load",
            "confluid.dump",
            "Trainer",
            "config/train.yaml",
            "pkg://not-installed/a.yaml",
        ]
        found = {m.text: m.target for m in mentions}
        assert found["base.yaml"] == Location(str((project / "config" / "base.yaml").resolve()), 0)
        assert found["missing.yaml"] is None and not mentions[1].deferred
        # a config: path is read from the project folder, a pkg:// value names a distribution's file under any key
        assert found["config/train.yaml"] == Location(str((project / "config" / "train.yaml").resolve()), 0)
        assert found["pkg://not-installed/a.yaml"] is None and not mentions[7].deferred
        assert _file_of(found["confluid.loader"]).name == "loader.py"
        load = found["confluid.loader.load"]
        assert load is not None and _file_of(load).read_text().splitlines()[load.line].startswith("def load(")
        assert _file_of(found["confluid.dump"]).name == "dumper.py"  # re-exported by confluid/__init__.py
        assert found["Trainer"] is None and mentions[5].deferred


def _file_of(target: Optional[Location]) -> Path:
    assert target is not None
    return Path(target.path)


if __name__ == "__main__":
    main()
