"""``hydraide`` — the command line of the preprocessor (``confluid/cli.py``, Click).

These pin the COMMAND surface: verbs, options, exit codes, the failure contract, and shell
completion — including ``--scope`` completing ``dimension=value`` from the DOCUMENT. What the
emitted document contains is ``tests/test_hydraide.py``'s job.
"""

import importlib
import sys
from pathlib import Path
from typing import Iterator, List

import pytest
from click.testing import CliRunner

import confluid
from confluid import configurable
from confluid.cli import hydraide

BASE = """\
model:
  _target_: CModel
  hidden: 32
lr: 0.1
"""

EXPERIMENT = """\
include: base.yaml
model.hidden: 64
torch_only:
  _scope_: {framework: torch}
  lr: 0.3
keras_only:
  _scope_: {framework: keras}
  lr: 0.5
"""


@configurable
class CModel:
    def __init__(self, hidden: int = 8, lr: float = 0.0) -> None:
        self.hidden = hidden
        self.lr = lr


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Sandboxed CWD/XDG/HOME (the search-path suites' pattern) with the two files written."""
    xdg_home = tmp_path / "xdg"
    xdg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.yaml").write_text(BASE)
    (tmp_path / "exp.yaml").write_text(EXPERIMENT)
    saved = confluid.get_app_name()
    yield xdg_home
    confluid.set_app_name(saved)


def _run(*argv: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(hydraide, list(argv))


# --------------------------------------------------------------------------- emit


def test_emit_prints_the_resolved_document_to_stdout() -> None:
    result = _run("emit", "exp.yaml")
    assert result.exit_code == 0, result.output
    assert result.stdout == confluid.hydraide.emit("exp.yaml")
    assert "_target_: CModel" in result.stdout and "hidden: 64" in result.stdout


def test_emit_honours_scope() -> None:
    torch = _run("emit", "exp.yaml", "--scope", "framework=torch")
    keras = _run("emit", "exp.yaml", "-s", "framework=keras")
    assert torch.exit_code == 0 and keras.exit_code == 0
    assert "lr: 0.3" in torch.stdout and "lr: 0.5" in keras.stdout


def test_emit_writes_the_file_named_by_output(tmp_path: Path) -> None:
    out = tmp_path / "resolved.yaml"
    result = _run("emit", "exp.yaml", "--output", str(out))
    assert result.exit_code == 0, result.output
    assert result.stdout == ""  # nothing on stdout when writing a file
    assert out.read_text() == confluid.hydraide.emit("exp.yaml")


def test_emit_resolves_a_relative_config_through_the_search_tiers(tmp_path: Path) -> None:
    """``hydraide emit experiment.yaml`` finds ``./config/experiment.yaml`` — the same tiers ``load`` uses."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tiered.yaml").write_text("x: 1\n")
    result = _run("emit", "tiered.yaml")
    assert result.exit_code == 0, result.output
    assert result.stdout == "x: 1\n"


# --------------------------------------------------------------------------- check


def test_check_passes_on_a_file_that_is_its_own_resolution(tmp_path: Path) -> None:
    (tmp_path / "resolved.yaml").write_text(confluid.hydraide.emit("exp.yaml"))
    result = _run("check", "resolved.yaml")
    assert result.exit_code == 0, result.output


def test_check_exits_1_with_a_diff_on_an_unresolved_file() -> None:
    result = _run("check", "exp.yaml")
    assert result.exit_code == 1
    assert result.stdout.startswith("---")  # a unified diff, on stdout


# --------------------------------------------------------------------------- failure contract


def test_a_located_config_error_is_one_line_on_stderr_exit_1(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("m:\n  _target_: X\n  _partial_: 3\n")
    result = _run("emit", "bad.yaml")
    assert result.exit_code == 1
    assert result.stdout == ""
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1 and "bad.yaml" in lines[0]  # located, one line, no traceback


def test_a_missing_config_is_a_usage_error() -> None:
    result = _run("emit", "nope.yaml")
    assert result.exit_code == 2
    result = _run("emit")
    assert result.exit_code == 2


# --------------------------------------------------------------------------- completion


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_prints_the_activation_script(shell: str) -> None:
    result = _run("completion", shell)
    assert result.exit_code == 0, result.output
    assert "_HYDRAIDE_COMPLETE" in result.stdout  # Click's completion protocol, for this program


def test_an_unknown_shell_is_a_usage_error() -> None:
    assert _run("completion", "powershell").exit_code == 2


def _complete(argv: List[str], incomplete: str, monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Drive Click's completion machinery the way a shell would, return the candidate values.

    Sets ``COMP_WORDS`` as every Click completion script does — the completer reads it when
    a dangling option has aborted the parse before the CONFIG argument was bound.
    """
    from click.shell_completion import ShellComplete

    monkeypatch.setenv("COMP_WORDS", " ".join(["hydraide", *argv, incomplete]))
    comp = ShellComplete(hydraide, {}, "hydraide", "_HYDRAIDE_COMPLETE")
    return [item.value for item in comp.get_completions(argv, incomplete)]


def test_scope_completes_dimension_values_from_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one completion worth having: ``--scope fram<TAB>`` offers what the CONFIG declares."""
    assert _complete(["emit", "exp.yaml", "--scope"], "", monkeypatch) == ["framework=keras", "framework=torch"]
    assert _complete(["emit", "exp.yaml", "--scope"], "framework=t", monkeypatch) == ["framework=torch"]
    assert _complete(["check", "exp.yaml", "-s"], "fr", monkeypatch) == ["framework=keras", "framework=torch"]


def test_scope_completion_is_empty_without_a_readable_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """CON: no config typed yet, or an unreadable one — offer nothing rather than crash the shell."""
    assert _complete(["emit", "--scope"], "", monkeypatch) == []
    assert _complete(["emit", "missing.yaml", "--scope"], "", monkeypatch) == []


def test_verbs_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(_complete([], "", monkeypatch)) >= {"emit", "check", "completion"}


# --------------------------------------------------------------------------- packaging


def test_the_console_script_is_declared_here() -> None:
    import tomllib

    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["hydraide"] == "confluid.cli:main"
    assert any(dep.startswith("click") for dep in pyproject["project"]["optional-dependencies"]["cli"])


def test_importing_the_cli_without_click_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.cli as cli_module

    monkeypatch.setitem(sys.modules, "click", None)  # makes `import click` raise ImportError
    with pytest.raises(ImportError, match=r"confluid\[cli\]"):
        importlib.reload(cli_module)
    monkeypatch.undo()
    importlib.reload(cli_module)  # restore the real module for the other tests


def test_a_yaml_syntax_error_is_one_line_and_exit_1(tmp_path: Path) -> None:
    """CD16 — a PyYAML parse error escaped as a ~69-line traceback; the CLI
    contract is ONE line on stderr, exit 1 (PyYAML's mark carries the location)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [1, 2\n")
    result = _run("emit", str(bad))
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "bad.yaml" in result.output


def test_an_unwritable_output_path_is_one_line_and_exit_1(tmp_path: Path) -> None:
    good = tmp_path / "ok.yaml"
    good.write_text("lr: 0.1\n")
    result = _run("emit", str(good), "-o", str(tmp_path / "nodir" / "out.yaml"))
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "cannot write" in result.output


def test_hydraide_prints_one_line_for_an_undecodable_file_and_for_an_engine_defect(tmp_path: Path) -> None:
    """CD14 (BUGS-2026-08-22) — a non-UTF-8 file is a located ConfigurationError from the
    loader, and ANY other exception still honours the one-line + exit 1 contract (a
    reference cycle currently dies with a RecursionError in the engine)."""
    import subprocess

    undecodable = tmp_path / "undecodable.yaml"
    undecodable.write_bytes(b"\xff\xfe\x00garbage: [\n")
    cycle = tmp_path / "cycle.yaml"
    cycle.write_text("a: !ref:b\nb: !ref:a\n")
    for path, needle in ((undecodable, "not a UTF-8 text file"), (cycle, "RecursionError")):
        run = subprocess.run(
            [sys.executable, "-m", "confluid.cli", "emit", str(path)], capture_output=True, text=True, cwd=tmp_path
        )
        lines = [line for line in run.stderr.splitlines() if "| INFO" not in line]
        assert run.returncode == 1
        assert len(lines) == 1 and needle in lines[0], lines
