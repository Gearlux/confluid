"""The reserved-key spelling → the tag spelling, line by line (record 19, phase 1b).

Confluid reads two spellings of one document (``docs/plain-format.md``,
``docs/targets.md``). The TAG form is the preferred way to WRITE a config; the
reserved-key form is what ``hydraide`` EMITS. This module goes the other way for
a file a human wants to edit again — a committed artefact, a config written
before the ruling — and it is the inverse of the 2026-08-11 codemod for the same
reason that one edited LINES: these files are ~86% comments, the comments are the
documentation, and a parse-and-rewrite would reformat all of it.

So the tag goes on the KEY line, the ``_target_:`` / ``_partial_:`` / ``_scope_:``
line is deleted, and every other byte — comments, order, spacing, quoting — is
left alone. Anything the grammar cannot convert is REPORTED with its line, never
guessed at.

Safety is not the grammar; it is the check. :func:`convert_file` writes a file
only when ``hydraide.emit(before) == hydraide.emit(after)`` under EVERY scope
activation the document declares — the property record 19 rests on.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

import yaml
from loggair import get_logger

from confluid.hydraide import emit

logger = get_logger("confluid.spelling")

_RESERVED = ("_target_", "_partial_", "_ref_", "_scope_", "_notscope_")
#: A value the inline ``(k=v)`` grammar carries verbatim: no space, comma, paren,
#: quote, brace or colon. Anything else rides a flow-mapping body after the tag.
_INLINE_SCALAR = re.compile(r"^[A-Za-z0-9_.\-/+]+$")
#: A whole-value ``${ref:path}`` ending the line's code — after ``key: `` or ``- ``.
#: NOT inside a flow container: PyYAML scans a tag up to whitespace, so
#: ``[!ref:a, !ref:b]`` reads ``ref:a,`` as the tag. ``${ref:}`` is legal in both
#: spellings and stays there.
_REF_PLACEHOLDER = re.compile(r"(?<=[:\-] )\$\{ref:([^}\s]+)\}(?=\s*$)")
#: ``key:`` with any ``- `` list prefixes; a key never opens a flow container.
_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<dash>(?:- )*)(?P<key>[^\s#{\[\-][^:#]*?):(?P<rest>.*)$")
_MERGE_REF = re.compile(r"^\s*(?:- )*<<:\s*(?P<value>.+?)\s*(?:#.*)?$")
#: The list-form scope block — a list whose FIRST item is the `_scope_` mapping — with
#: the dimension mapping written inline. Groups: everything up to the outer dash, the key, the spec.
_LIST_FORM_SCOPE = re.compile(r"^(?P<head>\s*(?:- )*)- - (?P<key>_(?:not)?scope_):(?P<spec>.*)$")


@dataclass
class Finding:
    """A site the line grammar could not convert. Reported, never guessed at."""

    path: str
    line: int
    text: str
    reason: str


@dataclass
class Result:
    """What :func:`convert_file` did to one file."""

    path: Path
    written: bool
    findings: List[Finding] = field(default_factory=list)
    activations_checked: int = 0


# --------------------------------------------------------------------------- #
# Small text helpers
# --------------------------------------------------------------------------- #


def _split_comment(line: str) -> Tuple[str, str]:
    """``(code, comment)`` — the first ``#`` outside quotes starts the comment."""
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


def _comment_with_gap(line: str) -> str:
    """The trailing ``   # comment`` of a line, gap included; ``""`` when it has none."""
    code, comment = _split_comment(line)
    return line[len(code.rstrip()) :] if comment else ""


def _is_blank_or_comment(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_flow_pairs(body: str) -> List[Tuple[str, str]]:
    """Top-level ``key: value`` pairs of a flow mapping ``{...}``, depth- and quote-aware."""
    inner = body.strip()
    assert inner.startswith("{") and inner.endswith("}"), body
    inner = inner[1:-1]
    pairs: List[Tuple[str, str]] = []
    depth = 0
    quote: Optional[str] = None
    start = 0
    for i, ch in enumerate(inner + ","):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            item = inner[start:i].strip()
            start = i + 1
            if not item:
                continue
            key, _, value = item.partition(":")
            pairs.append((key.strip(), value.strip()))
    return pairs


def _scope_suffix(spec_text: str) -> Optional[str]:
    """``framework=torch`` for ``{framework: torch}``; ``debug`` for ``{debug: }``; ``None`` if not one dim."""
    try:
        spec = yaml.safe_load(spec_text)
    except yaml.YAMLError:
        return None
    if not isinstance(spec, dict) or len(spec) != 1:
        return None
    ((dim, value),) = spec.items()
    if not isinstance(dim, str) or not dim.strip():
        return None
    if value is None:
        return dim.strip()
    text = str(value)
    if not re.match(r"^[A-Za-z0-9_.\-]+$", text):
        return None  # a value the tag suffix cannot carry
    return f"{dim.strip()}={text}"


# --------------------------------------------------------------------------- #
# Flow mappings: `key: {_target_: X, ...}` on one line
# --------------------------------------------------------------------------- #


def _convert_flow_value(
    value: str, findings: List[Finding], path: str, lineno: int, text: str, *, nested: bool = False
) -> Optional[str]:
    """The tag form of a flow-mapping VALUE carrying a reserved key, or ``None`` to leave it.

    ``nested`` — the value sits INSIDE a flow container. There a tag must be
    followed by whitespace (measured: ``{a: !c:N(h=8)}`` fails to scan), so the
    marker takes the flow-body form ``!class:N {h: 8}`` / ``!class:N {}``.
    """
    stripped = value.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    pairs = _split_flow_pairs(stripped)
    keys = {k for k, _ in pairs}
    if not (keys & set(_RESERVED)):
        return None
    if "<<" in keys:
        findings.append(
            Finding(path, lineno, text.strip(), "merge key on a marker (P2 idiom) has no line-for-line tag form")
        )
        return None

    if "_ref_" in keys:
        others = [(k, v) for k, v in pairs if k != "_ref_"]
        if others:
            findings.append(Finding(path, lineno, text.strip(), "_ref_ with kwargs — the tag form takes a bare path"))
            return None
        if nested:
            return None  # `{a: !ref:x}` does not scan; the mapping form is legal and stays
        return f"!ref:{dict(pairs)['_ref_'].strip(chr(34) + chr(39))}"  # a quoted flow value unquotes (PA22)

    if "_scope_" in keys or "_notscope_" in keys:
        skey = "_scope_" if "_scope_" in keys else "_notscope_"
        suffix = _scope_suffix(dict(pairs)[skey])
        if suffix is None:
            findings.append(
                Finding(
                    path,
                    lineno,
                    text.strip(),
                    f"{skey} with several dimensions (or a value a tag suffix cannot carry) — "
                    "the mapping form is the only spelling",
                )
            )
            return None
        scoped = [(k, v) for k, v in pairs if k != skey]
        tag = ("!scope:" if skey == "_scope_" else "!notscope:") + suffix
        if not scoped:
            return f"{tag} {{}}" if nested else tag
        return f"{tag} {{{', '.join(f'{k}: {v}' for k, v in scoped)}}}"

    if "_target_" not in keys:
        findings.append(Finding(path, lineno, text.strip(), "_partial_ without _target_ — refused by the loader too"))
        return None
    as_dict = dict(pairs)
    target = as_dict["_target_"].strip("\"'")
    partial = _yaml_true(as_dict.get("_partial_", "false"))
    tag = ("!partial:" if partial else "!class:") + target
    rest: List[Tuple[str, str]] = []
    for k, v in pairs:
        if k in ("_target_", "_partial_"):
            continue
        inner = _convert_flow_value(v, findings, path, lineno, text, nested=True)
        rest.append((k, inner if inner is not None else v))
    if not rest:
        return f"{tag} {{}}" if nested else tag
    if not nested and all(_INLINE_SCALAR.match(v) and _inline_round_trips(v) for _, v in rest):
        return f"{tag}({','.join(f'{k}={v}' for k, v in rest)})"
    return f"{tag} {{{', '.join(f'{k}: {v}' for k, v in rest)}}}"


# --------------------------------------------------------------------------- #
# The document walk
# --------------------------------------------------------------------------- #


def _direct_children(lines: Sequence[str], index: int, body_indent: int) -> List[int]:
    """Indices of the lines that are DIRECT children of a block opened at ``index``.

    A child sits at exactly ``body_indent``; a deeper line belongs to a child; a
    shallower non-blank, non-comment line closes the block.
    """
    out: List[int] = []
    for j in range(index + 1, len(lines)):
        line = lines[j]
        if _is_blank_or_comment(line):
            continue
        ind = _indent(line)
        if ind < body_indent:
            break
        if ind == body_indent:
            out.append(j)
    return out


def _body_indent_after(lines: Sequence[str], index: int) -> Optional[int]:
    """The indent of the first non-blank, non-comment line after ``index``, if deeper."""
    here = _indent(lines[index])
    dash = _KEY_LINE.match(lines[index])
    # A key on a dash line ("- k:") opens its body at the position after "- ".
    if dash and dash.group("dash"):
        here = len(dash.group("indent")) + len(dash.group("dash"))
    for j in range(index + 1, len(lines)):
        if _is_blank_or_comment(lines[j]):
            continue
        return _indent(lines[j]) if _indent(lines[j]) > here else None
    return None


def _yaml_true(text: str) -> bool:
    """``_partial_``'s truth by YAML's own boolean grammar (PA22: ``yes`` read as
    False here while the LOADER reads it True — the emitted tag went eager)."""
    try:
        return yaml.safe_load(str(text).strip()) is True
    except yaml.YAMLError:
        return False


def _inline_round_trips(scalar_text: str) -> bool:
    """May ``scalar_text`` ride the inline ``(k=v)`` form without changing meaning?

    The inline form re-coerces through ``parse_value`` (which reads ``None`` as
    null); the original document read the text with YAML's grammar (``None`` is
    a plain STRING). Emit inline only when the two readings agree — otherwise
    the flow-body form keeps YAML semantics (PA22).
    """
    from confluid.resolver import parse_value

    try:
        original_reading = yaml.safe_load(f"k: {scalar_text}")["k"]
    except Exception:  # noqa: BLE001 - unparsable inline text never rides the call form
        return False
    try:
        return bool(parse_value(scalar_text) == original_reading)
    except Exception:  # noqa: BLE001
        return False


def to_tags(text: str, *, path: str = "<config>") -> Tuple[str, List[Finding]]:
    """Rewrite ``text`` from the reserved-key spelling to the tag spelling.

    Returns ``(new_text, findings)``. A finding is a site left untouched; the
    text around it is still converted. Idempotent: a document already in tag form
    comes back byte-identical with no findings.
    """
    lines = text.split("\n")
    findings: List[Finding] = []

    # Pre-scan: anchors that a merge key references. A marker carrying such an
    # anchor must NOT be tagged — `<<: *b` merges the mapping's KEYS, and once
    # `_target_` is a tag instead of a key the merged node silently stops being a
    # marker (the P2 idiom). Both sides are reported.
    merged_anchors: Set[str] = set()
    for line in lines:
        m = _MERGE_REF.match(line)
        if m:
            merged_anchors.update(re.findall(r"\*([A-Za-z0-9_-]+)", m.group("value")))

    drop: Set[int] = set()
    replace: Dict[int, str] = {}

    for i, line in enumerate(lines):
        if i in drop or _is_blank_or_comment(line):
            continue
        code, comment_with_gap = _split_comment(line)[0], _comment_with_gap(line)
        code_stripped = code.rstrip()

        # ---- list-form scope block: `- - _scope_: {k: v}` -> `- !scope:k=v` -----------
        # The ONLY plain spelling of a conditional list ITEM. The body items (the
        # remaining `- ` lines of the inner list) stay as they are: in both spellings
        # a sequence body EXTENDS the surrounding list.
        lm = _LIST_FORM_SCOPE.match(code)
        if lm:
            suffix = _scope_suffix(lm.group("spec").strip()) if lm.group("spec").strip() else None
            if suffix is None:
                findings.append(
                    Finding(
                        path,
                        i + 1,
                        line.strip(),
                        f"{lm.group('key')} with several dimensions (or a value a tag suffix cannot carry) — "
                        "the mapping form is the only spelling",
                    )
                )
                continue
            tag = ("!scope:" if lm.group("key") == "_scope_" else "!notscope:") + suffix
            replace[i] = f"{lm.group('head')}- {tag}{comment_with_gap}"
            continue

        km = _KEY_LINE.match(code)
        is_item = code.lstrip().startswith("- ")
        if km is None and not is_item:
            continue
        # The value text after `key:` (or after a bare `- `), and its column.
        if km is not None:
            value = km.group("rest").strip()
            value_col = len(code_stripped) - len(value) if value else len(code_stripped)
        else:
            value = code.lstrip()[2:].strip()
            value_col = len(code_stripped) - len(value)

        # ---- flow: `key: {…reserved…}` / `- {…reserved…}` on one line ------------
        if value.startswith("{"):
            new_value = _convert_flow_value(value, findings, path, i + 1, line)
            if new_value is not None:
                replace[i] = f"{code[:value_col]}{new_value}{comment_with_gap}"
            continue
        if km is None:
            continue

        # ---- block: a key (or `- _target_:` item) whose body opens below -----------
        # A "- _target_: X" item: the marker's keys start ON the dash line, its
        # siblings sit at the column after "- ".
        dash_first_key = bool(km.group("dash")) and km.group("key") in _RESERVED
        value_wo_anchor = re.sub(r"^&\S+\s*", "", value)
        if not dash_first_key and value_wo_anchor:
            continue  # a scalar-valued key — nothing to convert (refs handled at the end)
        if dash_first_key:
            body_indent: Optional[int] = len(km.group("indent")) + len(km.group("dash"))
        else:
            body_indent = _body_indent_after(lines, i)
        if body_indent is None:
            continue

        children = _direct_children(lines, i, body_indent)
        entries: List[Tuple[int, str, str]] = []  # (line index, key, value)
        if dash_first_key:
            entries.append((i, km.group("key"), km.group("rest").strip()))
        for j in children:
            cm = _KEY_LINE.match(_split_comment(lines[j])[0])
            if cm is None or cm.group("dash"):
                entries = []  # a non-key child or a list item: this block is not a marker mapping
                break
            entries.append((j, cm.group("key").strip(), cm.group("rest").strip()))
        keys = {k for _, k, _ in entries}
        if not (keys & set(_RESERVED)):
            continue
        # A `<<:` beside a reserved key, or an anchor a merge references: report, leave.
        anchor = re.search(r"&([A-Za-z0-9_-]+)", code)
        if "<<" in keys or (anchor and anchor.group(1) in merged_anchors):
            findings.append(
                Finding(path, i + 1, line.strip(), "merge key on a marker (P2 idiom) has no line-for-line tag form")
            )
            continue

        by_key = {k: (j, v) for j, k, v in entries}
        consumed: List[int] = []
        if "_ref_" in by_key:
            if len(entries) > 1:
                findings.append(
                    Finding(path, i + 1, line.strip(), "_ref_ with kwargs — the tag form takes a bare path")
                )
                continue
            j, v = by_key["_ref_"]
            tag = f"!ref:{v.strip().strip(chr(34)).strip(chr(39))}"
            consumed.append(j)
        elif "_scope_" in by_key or "_notscope_" in by_key:
            skey = "_scope_" if "_scope_" in by_key else "_notscope_"
            j, v = by_key[skey]
            spec_text = v
            if not v:  # the dimension mapping is written as a block under the _scope_ line
                sub = _direct_children(lines, j, _body_indent_after(lines, j) or 0)
                spec_text = "{" + ", ".join(_split_comment(lines[s])[0].strip() for s in sub) + "}"
                consumed.extend(sub)
            suffix = _scope_suffix(spec_text)
            if suffix is None:
                findings.append(
                    Finding(
                        path,
                        j + 1,
                        lines[j].strip(),
                        f"{skey} with several dimensions (or a value a tag suffix cannot carry) — "
                        "the mapping form is the only spelling",
                    )
                )
                continue
            tag = ("!scope:" if skey == "_scope_" else "!notscope:") + suffix
            consumed.append(j)
        elif "_target_" in by_key:
            j, v = by_key["_target_"]
            target = v.strip().strip("\"'")
            partial = False
            consumed.append(j)
            if "_partial_" in by_key:
                pj, pv = by_key["_partial_"]
                partial = _yaml_true(pv)
                consumed.append(pj)
            tag = ("!partial:" if partial else "!class:") + target
        else:
            findings.append(
                Finding(path, i + 1, line.strip(), "_partial_ without _target_ — refused by the loader too")
            )
            continue

        # Rewrite the opening line: the tag right after the key (or after "- "), the
        # line's own comment kept in place, then the consumed lines' comments appended.
        trailing = "".join(_comment_with_gap(lines[j]) for j in consumed if j != i)
        if dash_first_key:
            new_code = code[: code.index("- ") + 2] + tag  # `- _target_: X   # c` -> `- !class:X   # c`
        else:
            new_code = f"{code_stripped} {tag}"
        replace[i] = f"{new_code}{comment_with_gap}{trailing}"
        drop.update(j for j in consumed if j != i)

    out: List[str] = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        line = replace.get(i, line)
        # Whole-value ${ref:...} placeholders → !ref: — outside quotes and comments only.
        code, comment = _split_comment(line)
        code = _REF_PLACEHOLDER.sub(lambda m: f"!ref:{m.group(1)}", code)
        out.append(code + comment)
    return "\n".join(out), findings


# --------------------------------------------------------------------------- #
# The corpus-run helper
# --------------------------------------------------------------------------- #


def _activations(raw_text: str) -> List[List[str]]:
    """The default activation plus one per declared dimension value."""
    from confluid.loader import ConfluidLoader
    from confluid.scopes import discover_dimension_values

    try:
        raw = yaml.load(raw_text, Loader=ConfluidLoader)
    except Exception:  # noqa: BLE001 - an unparseable file is caught by emit() below, with a location
        return [[]]
    acts: List[List[str]] = [[]]
    for dim, values in sorted(discover_dimension_values(raw).items()):
        for value in sorted(values):
            acts.append([f"{dim}={value}"])
    return acts


def convert_file(path: Union[str, Path], *, dry_run: bool = False) -> Result:
    """Convert one file in place — ONLY if the result resolves identically.

    ``hydraide.emit`` is run on the original and on the converted text (written
    to a sibling temp file, so relative ``include:`` targets resolve the same
    way) under every activation the document declares. Any difference, or any
    finding, leaves the file untouched.
    """
    path = Path(path)
    before = path.read_text()
    after, findings = to_tags(before, path=str(path))
    result = Result(path=path, written=False, findings=findings)
    if findings or after == before:
        return result

    tmp = path.with_name(f".{path.stem}.to_tags{path.suffix}")
    tmp.write_text(after)
    cwd = os.getcwd()
    try:
        os.chdir(path.parent)
        for scopes in _activations(before):
            result.activations_checked += 1
            if emit(path.name, scopes=scopes) != emit(tmp.name, scopes=scopes):
                result.findings.append(
                    Finding(str(path), 0, "", f"conversion is NOT equivalent under scopes={scopes} — left untouched")
                )
                return result
    except yaml.YAMLError as exc:
        # The converted text must never surface a third-party parser error from
        # the equivalence gate — report a Finding and leave the file untouched
        # (PA22: a quoted `_ref_` value once emitted `!ref:"proto"`, and
        # `convert_file` raised a raw ScannerError).
        result.findings.append(
            Finding(str(path), 0, "", f"converted text does not parse ({type(exc).__name__}) — left untouched")
        )
        return result
    finally:
        os.chdir(cwd)
        tmp.unlink(missing_ok=True)

    if not dry_run:
        path.write_text(after)
        result.written = True
        logger.debug(f"to_tags: {path} converted ({result.activations_checked} activations checked)")
    return result
