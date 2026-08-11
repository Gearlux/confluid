"""``confluid-migrate`` — rewrite tagged YAML configs into the plain-YAML format.

Converts the tag spelling (``!class:`` / ``!lazy:`` / ``!ref:`` / ``!clone:`` /
``!scope:``) into the reserved-key spelling (``_target_`` / ``_partial_`` /
``_ref_`` / ``_clone_`` / ``_scope_``), so the file becomes ordinary YAML that
``yaml.safe_load``, ``yq``, editor schemas and linters can read. See
``docs/plain-format.md``.

**It edits LINES, it does not parse-and-rewrite**, and that is the central design
decision. A round-trip through any YAML library reformats the whole document —
measured on a real 458-line config, a comment-preserving round-tripper changed 34
lines before doing any work at all, re-indenting every sequence item. These
configs are 86% comments and the comments carry the reasoning; a migration diff
has to be reviewable, so the only lines that may change are the ones being
converted. Editing lines also needs no dependency beyond the standard library,
which is what keeps this inside a package whose runtime deps are pyyaml, loggair
and typing-extensions.

**Safety comes from VERIFICATION, not from the parser.** Three checks, each
catching what the one before it cannot:

1. ``yaml.safe_load`` succeeds — the goal itself, and gross structural damage.
2. the document LOADS through confluid — malformed markers with a file:line. This
   is strictly stronger than the parse: a mis-indented block that turns a key into
   ``null`` and leaves ``_target_`` as a sibling of ``_scope_`` is *valid YAML* and
   is caught only here (measured, exactly that bug).
3. the resolved marker tree is IDENTICAL before and after, **per scope
   activation** — a wrong verdict inside a ``keras:`` block never appears in a
   no-scope resolve, because the block is dropped before markers are built.

Anything the line grammar cannot convert is REPORTED, never guessed at: the final
pass re-scans for surviving tags and emits a finding per site.

Usage::

    confluid-migrate config/*.yaml              # rewrite in place
    confluid-migrate config/ --check            # exit 1 if anything would change
    confluid-migrate config/ --report out.csv   # per-site record of every change
    confluid-migrate config/ --verify           # + the marker-tree equivalence check
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loggair import get_logger

from confluid.resolver import _ENV_RESOLVERS, _MARKER_RESOLVERS, _TARGET_CALL_RE, _split_inline_pairs

logger = get_logger("confluid.migrate")

#: A tag at the start of a value, e.g. ``!class:pkg.Thing(a=1)``.
_TAG_RE = re.compile(r"!(?P<kind>class|lazy|ref|clone|scope|notscope):(?P<suffix>\S*)")

#: Everything after the tag is captured raw and split by :func:`_split_flow` —
#: a tag may be followed by an inline FLOW mapping (``!class:X {}`` /
#: ``!class:X {a: 1}``), which a regex cannot delimit correctly because the value
#: may itself contain braces: waivefront writes
#: ``{ low_level: "-{reference_snr_level}" }``, a per-record template inside a
#: quoted string, and a ``\{[^}]*\}`` pattern stops at the wrong brace.
_TAG_TAIL = r"(?P<tag>!(?:class|lazy|ref|clone|notscope|scope):\S*)(?P<rest>.*)$"
_TRAILING = ""

#: ``key: !tag:...`` — the key may be quoted or carry dots/globs.
_KEY_TAG_RE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[^\s#][^:]*):[ ]+" + _TAG_TAIL + _TRAILING)

#: ``- !tag:...`` — a tagged sequence item.
_ITEM_TAG_RE = re.compile(r"^(?P<indent>[ ]*)-[ ]+" + _TAG_TAIL + _TRAILING)

#: A tag ALONE on its line, applying to the block that follows at the SAME indent::
#:
#:     stream:
#:       !class:recordstream.Stream
#:       source: ...
#:
#: Valid YAML and common in real configs. Its keys go at the TAG's own indent, not
#: one level deeper — the body is already there.
_LONE_TAG_RE = re.compile(r"^(?P<indent>[ ]*)" + _TAG_TAIL + _TRAILING)

#: Any surviving tag, for the post-pass sweep.
_ANY_TAG_RE = re.compile(r"!(?:class|lazy|ref|clone|scope|notscope)[:\s]")

#: ``${NAME}`` / ``${NAME:default}`` with a PLAIN name — an environment read under
#: today's rules (a dotted/bracketed name is a config key and is left alone).
_BRACED_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

#: The resolver names a ``${name:arg}`` placeholder may already carry. Shared with
#: the resolver so the two cannot drift.
_RESOLVER_NAMES = _ENV_RESOLVERS | _MARKER_RESOLVERS

#: Bare ``$NAME`` — environment-only, no dotted form and no default.
_BARE_ENV_RE = re.compile(r"(?<![\w$}])\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class Finding:
    """A site the line grammar could not convert. Reported, never guessed at."""

    path: str
    line: int
    text: str
    reason: str


@dataclass
class Result:
    """What one file's migration did."""

    path: Path
    original: str
    migrated: str
    conversions: List[Tuple[int, str, str]] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original != self.migrated


def _split_flow(rest: str) -> Tuple[Optional[str], str]:
    """Split the text after a tag into ``(flow_mapping, trailing)``.

    Depth- and quote-aware: a brace inside a quoted scalar is DATA, and the value
    of a flow key may carry its own braces (a per-record template). An unbalanced
    or absent ``{`` yields ``(None, rest)`` so the caller treats the line as
    having no flow mapping at all.
    """
    stripped = rest.lstrip()
    if not stripped.startswith("{"):
        return None, rest
    offset = len(rest) - len(stripped)
    depth, quote = 0, None
    for i, ch in enumerate(stripped):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[: i + 1], rest[offset + i + 1 :]
    return None, rest  # unbalanced — leave it for the surviving-tag sweep


def _split_comment(line: str) -> Tuple[str, str]:
    """Split ``line`` into ``(code, comment)`` at an unquoted ``#``.

    Quote-aware, because a ``#`` inside a quoted scalar is DATA — a naive split
    would treat half a path as a comment and leave it unconverted.
    """
    quote: Optional[str] = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i], line[i:]
    return line, ""


def _is_comment(line: str) -> bool:
    """True for a blank line or one whose first non-space character is ``#``."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _body_indent(lines: Sequence[str], start: int) -> Optional[int]:
    """Indent of the block belonging to the construct on line ``start``, if any.

    Looks ahead past blank and comment lines for the next content line; a deeper
    indent means the construct carries a body.
    """
    own = len(lines[start]) - len(lines[start].lstrip())
    for line in lines[start + 1 :]:
        if _is_comment(line):
            continue
        indent = len(line) - len(line.lstrip())
        return indent if indent > own else None
    return None


def _body_is_sequence(lines: Sequence[str], start: int) -> bool:
    """Whether the body under line ``start`` is a SEQUENCE rather than a mapping."""
    for line in lines[start + 1 :]:
        if _is_comment(line):
            continue
        return line.lstrip().startswith("- ")
    return False


def _inline_kwarg_lines(args: str, indent: str) -> List[str]:
    """Render a tag's inline ``(k=v)`` kwargs as YAML lines at ``indent``.

    The split is the ONE shared grammar (``resolver._split_inline_pairs``), and the
    value is emitted VERBATIM — the tag form coerces it with ``parse_value``, which
    is ``yaml.safe_load`` of the same text, so writing the text back out re-reads
    to the identical value.
    """
    return [f"{indent}{key}: {value}" for key, value in _split_inline_pairs(args)]


def _target_and_kwargs(suffix: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split a ``!class:`` / ``!lazy:`` suffix into ``(target, inline_kwargs)``."""
    call = _TARGET_CALL_RE.match(suffix)
    if call:
        return call.group(1), _split_inline_pairs(call.group(2))
    return suffix, []


def _convert_tag_line(
    lines: Sequence[str],
    index: int,
    path: str,
    findings: List[Finding],
) -> Optional[List[str]]:
    """Convert one tagged line to its reserved-key form, or return ``None``.

    ``None`` means "not a shape this grammar handles" — the caller keeps the line
    and the post-pass sweep reports it.
    """
    line = lines[index]
    key_match = _KEY_TAG_RE.match(line)
    item_match = None if key_match else _ITEM_TAG_RE.match(line)
    lone_match = None if (key_match or item_match) else _LONE_TAG_RE.match(line)
    match = key_match or item_match or lone_match
    if match is None:
        return None

    tag = _TAG_RE.fullmatch(match.group("tag"))
    if tag is None:
        return None
    kind, suffix = tag.group("kind"), tag.group("suffix")
    indent = match.group("indent")
    flow, trailing = _split_flow(match.group("rest") or "")
    if flow is None and trailing.strip() and not trailing.lstrip().startswith("#"):
        return None  # text after the tag this grammar does not model — report it

    # Where the construct's own keys go. A LONE tag's body already sits at the
    # tag's own indent, so its keys join it there; the other two forms open a new
    # level under the head line.
    body = indent if lone_match else f"{indent}  "
    head = f"{indent}{match.group('key')}:" if key_match else f"{indent}-"

    def emit(keys: List[str]) -> List[str]:
        """Head line plus the construct's keys, in the idiomatic layout.

        A sequence item carries its FIRST key on the dash line (``- _target_: X``)
        rather than leaving a bare ``-``; both parse identically, but every config
        in the wild is written the compact way and a migration should not restyle
        what it touches. A lone tag has no head line at all.
        """
        if flow is not None:
            # Preserve the author's compact flow style: `- !class:X {a: 1}` keeps
            # its braces rather than being exploded over four lines.
            inner = flow[1:-1].strip()
            merged = ", ".join(filter(None, [k.strip() for k in keys] + ([inner] if inner else [])))
            one = f"{{{merged}}}"
            if lone_match:
                return [f"{indent}{one}{trailing}"]
            return [f"{head} {one}{trailing}" if key_match else f"{indent}- {one}{trailing}"]
        if lone_match:
            return [f"{keys[0]}{trailing}"] + keys[1:] if keys else []
        if key_match or not keys:
            return [f"{head}{trailing}"] + keys
        first = keys[0][len(body) :]
        return [f"{indent}- {first}{trailing}"] + keys[1:]

    if kind in ("ref", "clone"):
        path_arg, extra = _target_and_kwargs(suffix)
        if extra or _body_indent(lines, index) is not None:
            # Carries kwargs — only the mapping form can hold them.
            call = _TARGET_CALL_RE.match(suffix)
            keys = [f"{body}_{kind}_: {path_arg}"]
            keys += _inline_kwarg_lines(call.group(2) if call else "", body)
            return emit(keys)
        # The scalar shorthand keeps the value on one line.
        marker = f"${{{kind}:{path_arg}}}"
        if lone_match:
            return [f"{indent}{marker}{trailing}"]
        return [f"{head} {marker}{trailing}" if key_match else f"{indent}- {marker}{trailing}"]

    if kind in ("scope", "notscope"):
        if _body_indent(lines, index) is not None and _body_is_sequence(lines, index):
            findings.append(
                Finding(path, index + 1, line.strip(), "scope block with a SEQUENCE body needs _content_ by hand")
            )
            return None
        return emit([f"{body}_{kind}_: {suffix}"])

    # class / lazy. An ``@axis=value`` selector needs NO special handling: it lives
    # in the target STRING, not in tag syntax, so ``_target_: Loss@framework=keras``
    # is ordinary YAML and ``registry.parse_target_spec`` reads it exactly as before
    # (the ``$key`` document form included).
    target, _pairs = _target_and_kwargs(suffix)
    call = _TARGET_CALL_RE.match(suffix)
    keys = [f"{body}_target_: {target}"]
    if kind == "lazy":
        keys.append(f"{body}_partial_: true")
    keys += _inline_kwarg_lines(call.group(2) if call else "", body)
    return emit(keys)


def _convert_interpolation(code: str) -> str:
    """Make every environment read EXPLICIT: ``${NAME}`` / ``$NAME`` -> ``${env:NAME}``.

    Meaning-preserving by construction: under today's rules a PLAIN-named
    ``${...}`` is always an environment variable and a dotted/bracketed one is
    always a config key, so rewriting only the plain names changes nothing. It is
    done now so that the eventual flip of a bare ``${NAME}`` to mean a config key
    (phase 5) finds no ambiguous spellings left in any config.
    """

    def braced(m: "re.Match[str]") -> str:
        name, default = m.group(1), m.group(2)
        if default is not None and name in _RESOLVER_NAMES:
            # Already a resolver call (``${env:X}`` / ``${ref:x}``) — the regex
            # cannot tell it apart from ``${NAME:default}`` by shape, so name it.
            # Without this the pass is not idempotent: ``${env:X}`` reads as name
            # "env" with default "X" and becomes ``${env:env,X}``.
            return m.group(0)
        return f"${{env:{name},{default}}}" if default is not None else f"${{env:{name}}}"

    return _BARE_ENV_RE.sub(lambda m: f"${{env:{m.group(1)}}}", _BRACED_ENV_RE.sub(braced, code))


def migrate_text(text: str, *, path: str = "<config>") -> Tuple[str, List[Tuple[int, str, str]], List[Finding]]:
    """Convert one document's text. Returns ``(migrated, conversions, findings)``.

    Pure: imports nothing from the config's own packages, so it runs against a
    checkout whose frameworks are not installed.
    """
    lines = text.splitlines()
    out: List[str] = []
    conversions: List[Tuple[int, str, str]] = []
    findings: List[Finding] = []

    for index, line in enumerate(lines):
        if _is_comment(line):
            out.append(line)  # a comment is documentation, never a node
            continue

        replacement = _convert_tag_line(lines, index, path, findings) if "!" in line else None
        if replacement is not None:
            conversions.append((index + 1, line.strip(), " / ".join(r.strip() for r in replacement)))
            out.extend(replacement)
            continue

        code, comment = _split_comment(line)
        converted = _convert_interpolation(code)
        if converted != code:
            conversions.append((index + 1, line.strip(), (converted + comment).strip()))
        out.append(converted + comment)

    migrated = "\n".join(out) + ("\n" if text.endswith("\n") else "")

    # Nothing is left behind silently: anything still carrying a tag is reported.
    for index, line in enumerate(migrated.splitlines()):
        if _is_comment(line):
            continue
        code, _ = _split_comment(line)
        if _ANY_TAG_RE.search(code) and not any(f.line == index + 1 for f in findings):
            findings.append(Finding(path, index + 1, line.strip(), "tag survived conversion — convert by hand"))

    return migrated, conversions, findings


# --------------------------------------------------------------------------- #
# Verification — the marker-tree equivalence check
# --------------------------------------------------------------------------- #


def _marker_shape(value: Any) -> Any:
    """An identity-free structural view of a resolved node, for comparison."""
    from confluid.fluid import Clone, Fluid, Reference, Target

    if isinstance(value, Target):
        return {
            "kind": "Partial" if value.partial else "Target",
            "target": str(value.target),
            "kwargs": {k: _marker_shape(v) for k, v in sorted(value.kwargs.items())},
        }
    if isinstance(value, (Reference, Clone)):
        return {"kind": type(value).__name__, "target": str(value.target)}
    if isinstance(value, Fluid):
        return {"kind": type(value).__name__, "target": str(value.target)}
    if isinstance(value, dict):
        return {k: _marker_shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_marker_shape(v) for v in value]
    return value


def _activations(raw: Dict[str, Any]) -> List[List[str]]:
    """Every scope activation worth comparing under: the default plus each value.

    A wrong conversion inside an inactive block is INVISIBLE to a no-scope
    resolve — the block is dropped before any marker is built — so the comparison
    has to be run once per declared value as well.
    """
    from confluid.scopes import discover_dimension_values

    out: List[List[str]] = [[]]
    for dimension, values in sorted(discover_dimension_values(raw).items()):
        out.extend([f"{dimension}={value}"] for value in sorted(values))
    return out


def _resolved(text: str, scopes: Sequence[str]) -> Any:
    from confluid.engine import resolve
    from confluid.loader import load

    return _marker_shape(resolve(load(text, flow=False, scopes=list(scopes))))


def verify_equivalence(before: str, after: str, *, path: str = "<config>") -> List[str]:
    """Compare the two spellings' RESOLVED marker trees. Returns problem strings.

    Empty means the documents are equivalent under every activation they declare.
    Requires the config's own classes to be importable; an ImportError is returned
    as a problem rather than raised, so a partial checkout reports rather than dies.
    """
    import yaml

    from confluid.loader import ConfluidLoader

    problems: List[str] = []
    try:
        yaml.safe_load(after)
    except yaml.YAMLError as exc:
        return [f"{path}: not valid plain YAML after conversion — {str(exc).splitlines()[0]}"]

    try:
        # The RAW parse — scope blocks still present as markers. `yaml.safe_load`
        # cannot see one, so asking it for the dimensions returns nothing and the
        # per-activation pass silently degrades to the default alone.
        raw = yaml.load(before, Loader=ConfluidLoader) or {}
        activations = _activations(raw) if isinstance(raw, dict) else [[]]
    except Exception:  # noqa: BLE001 - a scope scan must never block the comparison
        activations = [[]]

    for scopes in activations:
        label = ",".join(scopes) or "<no scopes>"
        try:
            if _resolved(before, scopes) != _resolved(after, scopes):
                problems.append(f"{path}: marker trees DIFFER under {label}")
        except ImportError as exc:
            problems.append(f"{path}: cannot verify under {label} (not importable: {exc})")
        except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
            problems.append(f"{path}: {type(exc).__name__} under {label}: {str(exc).splitlines()[0]}")
    return problems


# --------------------------------------------------------------------------- #
# File + CLI plumbing
# --------------------------------------------------------------------------- #


def migrate_file(path: Path, *, write: bool = True, verify: bool = False) -> Tuple[Result, List[str]]:
    """Migrate one file. Returns ``(result, problems)``; nothing is written on a problem."""
    original = path.read_text()
    migrated, conversions, findings = migrate_text(original, path=str(path))
    result = Result(path=path, original=original, migrated=migrated, conversions=conversions, findings=findings)

    problems: List[str] = []
    if result.changed and verify:
        problems = verify_equivalence(original, migrated, path=str(path))
    if write and result.changed and not problems:
        path.write_text(migrated)
    return result, problems


def _yaml_files(paths: Sequence[str]) -> List[Path]:
    found: List[Path] = []
    for entry in paths:
        p = Path(entry)
        found.extend(sorted(q for q in p.rglob("*.y*ml") if q.is_file()) if p.is_dir() else [p])
    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for ``confluid-migrate``."""
    parser = argparse.ArgumentParser(
        prog="confluid-migrate",
        description="Rewrite tagged confluid YAML configs into the plain-YAML (_target_) format.",
    )
    parser.add_argument("paths", nargs="+", help="YAML files, or directories to search recursively")
    parser.add_argument("--check", action="store_true", help="report what would change and exit 1; write nothing")
    parser.add_argument("--verify", action="store_true", help="also compare resolved marker trees before/after")
    parser.add_argument("--report", metavar="CSV", help="write a per-site record of every conversion")
    args = parser.parse_args(argv)

    files = _yaml_files(args.paths)
    if not files:
        logger.warning(f"no YAML files under {', '.join(args.paths)}")
        return 0

    changed: List[Result] = []
    all_findings: List[Finding] = []
    all_problems: List[str] = []
    rows: List[Tuple[str, int, str, str]] = []

    for path in files:
        result, problems = migrate_file(path, write=not args.check, verify=args.verify)
        all_findings.extend(result.findings)
        all_problems.extend(problems)
        if result.changed:
            changed.append(result)
            rows.extend((str(path), line, before, after) for line, before, after in result.conversions)

    if args.report:
        with open(args.report, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["file", "line", "before", "after"])
            writer.writerows(rows)
        logger.info(f"wrote {len(rows)} conversions to {args.report}")

    verb = "would convert" if args.check else "converted"
    logger.info(f"{verb} {len(rows)} sites across {len(changed)} of {len(files)} files")

    for finding in all_findings:
        logger.warning(f"{finding.path}:{finding.line}: {finding.reason} — {finding.text}")
    for problem in all_problems:
        logger.error(problem)

    if all_problems:
        logger.error("NOTHING was written for the files above — their conversion is not equivalent")
        return 2
    if args.check and changed:
        return 1
    return 1 if all_findings else 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    sys.exit(main())
