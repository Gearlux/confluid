"""``${...}`` interpolation & the include tree — companion to ``docs/interpolation.md``.

Writes a two-file include tree to a temp directory, then shows env-var
interpolation (``${VAR}``), config-key interpolation (``${dotted.path}``, native
type preserved on a whole-string match), and ``load_config_with_paths`` returning
every contributing YAML file. Note interpolation is applied at MATERIALIZATION
(``load`` / ``materialize`` / ``resolve``) — ``load_config`` returns the raw parse.
"""

import os
import tempfile
from pathlib import Path

from confluid import load, load_config_with_paths


def main() -> None:
    os.environ["EXAMPLE_DATA_ROOT"] = "/store"

    with tempfile.TemporaryDirectory() as tmp:
        common = Path(tmp) / "common.yaml"
        common.write_text(
            """
train:
  dataset: RFUAV
  version: v3
  epochs: 20
"""
        )
        experiment = Path(tmp) / "experiment.yaml"
        experiment.write_text(
            """
include: common.yaml
# Mix env + config keys in one string; embedded matches substitute str(value):
data_dir: "${EXAMPLE_DATA_ROOT}/${train.dataset}/${train.version}/data"
# A whole-string match keeps the native type (int, not "20"):
epochs: "${train.epochs}"
# A miss falls back to the :default
port: "${EXAMPLE_MISSING_PORT:8080}"
"""
        )

        # The include tree: entrypoint first, then each transitively include:-d file.
        raw, paths = load_config_with_paths(experiment)
        assert "${EXAMPLE_DATA_ROOT}" in raw["data_dir"], "load_config returns the RAW parse"

        # Interpolation happens at materialization — load() the same file.
        data = load(str(experiment))

        assert data["data_dir"] == "/store/RFUAV/v3/data", data["data_dir"]
        assert data["epochs"] == 20 and isinstance(data["epochs"], int), "whole-string match keeps the int"
        assert data["port"] == 8080, "missing name -> the :default applies (coerced)"
        assert [p.name for p in paths] == ["experiment.yaml", "common.yaml"], "entrypoint first, then includes"

        print(f"data_dir: {data['data_dir']}")
        print(f"epochs:   {data['epochs']} ({type(data['epochs']).__name__})")
        print(f"port:     {data['port']} (from :default)")
        print(f"include tree: {[p.name for p in paths]}")


if __name__ == "__main__":
    main()
