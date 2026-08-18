"""``hydraide`` — the command line of the preprocessor (Click; ``confluid[cli]``).

The FUNCTIONS live in :mod:`confluid.hydraide` (``emit`` / ``check``); this module is only
their command surface::

    hydraide emit experiment.yaml --scope framework=torch        # the resolved document, on stdout
    hydraide emit experiment.yaml -o resolved.yaml               # … or written to a file
    hydraide check resolved.yaml                                 # exit 1 + unified diff unless the file
                                                                 # is its own resolution (a CI gate)
    eval "$(hydraide completion zsh)"                            # once, in the shell rc — then
    hydraide emit experiment.yaml --scope fram<TAB>              # completes framework=torch / =keras
                                                                 # from what the DOCUMENT declares

Contract: a located ``ConfluidError`` renders as ONE line on stderr and exits 1 (a preprocessor
must not degrade what the loader refuses); a usage error exits 2 (Click); ``check`` with a
stale file exits 1 with the diff on stdout. A relative config resolves through the same search
tiers ``load()`` uses (CWD → ``./config/`` → XDG), so ``hydraide emit experiment.yaml`` finds
``./config/experiment.yaml``.

Click is the OPTIONAL ``confluid[cli]`` extra: the console script is always declared, and
importing this module without Click raises an ``ImportError`` naming the extra — the same
pattern as ``confluid[pydantic]``.
"""

import os
import shlex
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import click
    from click.shell_completion import CompletionItem, get_completion_class
except ImportError as exc:  # pragma: no cover — exercised by the test that hides click
    raise ImportError(
        "the hydraide command line requires click, which is an optional dependency — "
        "install the extra: pip install 'confluid[cli]'"
    ) from exc

from confluid.exceptions import ConfluidError
from confluid.hydraide import check as _check
from confluid.hydraide import emit as _emit
from confluid.loader import load, resolve_config_path
from confluid.scopes import discover_dimension_values

__all__ = ["hydraide", "main"]

_PROG = "hydraide"
_COMPLETE_VAR = "_HYDRAIDE_COMPLETE"  # Click derives it from the program name; spelled once here


def _resolve_config(ctx: click.Context, param: click.Parameter, value: str) -> Path:
    """The CONFIG argument through the search tiers; a total miss is a USAGE error (exit 2)."""
    resolved = resolve_config_path(value)
    if not resolved.exists():
        raise click.BadParameter(f"config file not found: {value}", ctx=ctx, param=param)
    return resolved


def _config_on_the_command_line(ctx: click.Context) -> Optional[Path]:
    """The CONFIG the user has already typed, while completing a later word.

    ``ctx.params["config"]`` when Click bound it — but a DANGLING option (``… exp.yaml --scope
    <TAB>``) makes the resilient parse abort on the missing option value BEFORE the positional
    argument is bound, so during exactly the completion this exists for it is ``None``. Click's
    completion scripts (bash/zsh/fish alike) export the shell's word list as ``COMP_WORDS``;
    the first word naming an existing config file is the answer.
    """
    config = ctx.params.get("config")
    if config is not None:
        return Path(config)
    for word in shlex.split(os.environ.get("COMP_WORDS", "")):
        if word.endswith((".yaml", ".yml")):
            candidate = resolve_config_path(word)
            if candidate.exists():
                return candidate
    return None


def _complete_scope(ctx: click.Context, param: click.Parameter, incomplete: str) -> List[CompletionItem]:
    """``--scope`` candidates — ``dimension=value`` for every value the CONFIG declares.

    Reads the document at ``until="raw"`` (scope blocks intact — that is what discovery needs)
    and offers what :func:`confluid.discover_dimension_values` finds. A boolean dimension
    (declared without values) is offered bare. No config yet, or an unreadable one → nothing:
    completion must never crash the shell.
    """
    config = _config_on_the_command_line(ctx)
    if config is None:
        return []
    try:
        dimensions = discover_dimension_values(load(config, until="raw"))
    except Exception:  # noqa: BLE001 — a shell completion swallows everything
        return []
    candidates: List[str] = []
    for dim, values in sorted(dimensions.items()):
        candidates.extend(f"{dim}={v}" for v in sorted(values)) if values else candidates.append(dim)
    return [CompletionItem(c) for c in candidates if c.startswith(incomplete)]


def _config_argument(fn):  # type: ignore[no-untyped-def]
    return click.argument(
        "config",
        type=click.Path(dir_okay=False),  # no `exists=`: the callback probes the search tiers first
        callback=_resolve_config,
    )(fn)


def _scope_option(fn):  # type: ignore[no-untyped-def]
    return click.option(
        "--scope",
        "-s",
        multiple=True,
        metavar="DIM=VALUE",
        shell_complete=_complete_scope,
        help="Activate a scope (repeatable): `--scope framework=torch`, `--scope debug`.",
    )(fn)


@click.group(name=_PROG)
def hydraide() -> None:
    """Resolve a confluid config to ONE plain-YAML document — includes spliced, scopes applied,
    dotted keys expanded, interpolation burned in, broadcasting settled, shared markers anchored."""


@hydraide.command()
@_config_argument
@_scope_option
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write the document here instead of stdout.")
def emit(config: Path, scope: Tuple[str, ...], output: Optional[str]) -> None:
    """Resolve CONFIG and print the plain-YAML document (or write it with --output)."""
    try:
        text = _emit(config, scopes=list(scope) or None)
    except ConfluidError as exc:
        raise click.ClickException(str(exc)) from exc  # one line on stderr, exit 1
    if output:
        Path(output).write_text(text)
    else:
        click.echo(text, nl=False)


@hydraide.command()
@_config_argument
@_scope_option
def check(config: Path, scope: Tuple[str, ...]) -> None:
    """Exit 1 (unified diff on stdout) unless CONFIG is its OWN resolution — a committed artefact's CI gate."""
    try:
        diff = _check(config, scopes=list(scope) or None)
    except ConfluidError as exc:
        raise click.ClickException(str(exc)) from exc
    if diff is not None:
        click.echo(diff, nl=False)
        sys.exit(1)


@hydraide.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str) -> None:
    """Print the shell-completion script: `eval "$(hydraide completion zsh)"` in your rc file."""
    cls = get_completion_class(shell)
    assert cls is not None  # the Choice above guarantees a supported shell
    click.echo(cls(hydraide, {}, _PROG, _COMPLETE_VAR).source())


def main() -> None:
    hydraide(prog_name=_PROG)


if __name__ == "__main__":
    main()
