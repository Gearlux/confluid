"""Config-file search paths (XDG) — companion to ``docs/search-paths.md``.

Builds a sandboxed ``XDG_CONFIG_HOME`` in a temp directory and demonstrates
the resolution tiers for a relative config path — CWD -> ./config/ -> the XDG
base dirs — plus the app-name namespacing (``set_app_name``) and the public
resolver ``resolve_config_path``. The real ``~/.config`` is never touched.
"""

import os
import tempfile
from pathlib import Path

from confluid import load_config, load_config_with_paths, resolve_config_path, set_app_name


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Sandbox every location the search consults.
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["XDG_CONFIG_DIRS"] = str(root / "xdg_sys")
        cwd = root / "project"
        cwd.mkdir()
        os.chdir(cwd)

        # A shared include living under the app's XDG config dir...
        set_app_name("my-app")
        app_dir = root / "xdg" / "my-app"
        app_dir.mkdir(parents=True)
        (app_dir / "common.yaml").write_text("lr: 0.001\nwho: xdg\n")

        # ...pulled in by a local experiment config with no local common.yaml.
        (cwd / "experiment.yaml").write_text("include: common.yaml\nepochs: 5\n")

        data, paths = load_config_with_paths("experiment.yaml")
        assert data == {"lr": 0.001, "who": "xdg", "epochs": 5}, data
        assert paths[1] == (app_dir / "common.yaml").resolve(), "the RESOLVED location is recorded"
        print(f"include resolved to: {paths[1]}")

        # Local tiers always win: a ./config/ twin now shadows the XDG file.
        (cwd / "config").mkdir()
        (cwd / "config" / "common.yaml").write_text("lr: 0.01\nwho: cwd_config\n")
        assert load_config("experiment.yaml")["who"] == "cwd_config"
        print(f"./config/ shadows XDG: who={load_config('experiment.yaml')['who']}")

        # The public resolver returns the first existing candidate...
        # (compare against Path.cwd(): macOS temp dirs are symlinked /var -> /private/var)
        assert resolve_config_path("common.yaml") == Path.cwd() / "config" / "common.yaml"
        # ...and the input path verbatim on a total miss.
        assert resolve_config_path("missing.yaml") == Path("missing.yaml")
        print(f"resolve_config_path('common.yaml') -> {resolve_config_path('common.yaml')}")


if __name__ == "__main__":
    main()
