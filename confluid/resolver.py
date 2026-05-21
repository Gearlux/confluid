import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import yaml

from logflow import get_logger

logger = get_logger("confluid.resolver")


_PathSegment = Tuple[str, Union[str, int]]
"""``(kind, value)`` — kind is one of ``"key"`` / ``"idx"`` / ``"idxref"``."""

# Token = either a bareword (``\.?(\w[\w-]*)``, optionally preceded by a dot)
# or a bracketed ``[...]`` group with non-empty inner content.
_PATH_TOKEN_RE = re.compile(r"\.?(\w[\w-]*)|\[([^\[\]]+)\]")
_INT_LITERAL_RE = re.compile(r"-?\d+")


def _parse_path_segments(path: str) -> Optional[List[_PathSegment]]:
    """Tokenize a reference path into a flat list of segments.

    ``a.b[0].c[idx]`` →
    ``[("key","a"), ("key","b"), ("idx",0), ("key","c"), ("idxref","idx")]``.

    A bareword that's all-digits (e.g. the ``0`` in ``items.0``) is treated
    as an integer index — same disambiguation as the bracket form.
    Bracketed contents that aren't an integer literal become an
    ``"idxref"`` segment whose value is resolved against the walking
    context at lookup time.

    Returns ``None`` for paths that don't fully tokenize, so the caller
    can treat malformed input as "unresolved" without raising.
    """
    segments: List[_PathSegment] = []
    pos = 0
    n = len(path)
    while pos < n:
        m = _PATH_TOKEN_RE.match(path, pos)
        if not m or m.end() == pos:
            return None
        bare, bracket = m.group(1), m.group(2)
        if bracket is not None:
            inner = bracket.strip()
            if _INT_LITERAL_RE.fullmatch(inner):
                segments.append(("idx", int(inner)))
            else:
                segments.append(("idxref", inner))
        else:
            assert bare is not None
            if _INT_LITERAL_RE.fullmatch(bare):
                segments.append(("idx", int(bare)))
            else:
                segments.append(("key", bare))
        pos = m.end()
    return segments


def _walk_path_segments(
    segments: List[_PathSegment],
    context: Any,
    lookup_fn: Callable[[str, Dict[str, Any]], Any],
) -> Any:
    """Walk pre-tokenized segments through nested dicts and lists.

    For ``"idxref"`` segments, ``lookup_fn(name, context)`` is called with
    the *original* context (not the current cursor) so the inner name
    resolves in the same scope as the outer reference. The resolved value
    decides the step semantics: ``int`` → list index, ``str``/``int``
    → dict key.

    Returns ``None`` when the walk can't proceed (key missing, index
    out of range, type mismatch), preserving the caller's
    "missing → None" contract.
    """
    current: Any = context
    for kind, val in segments:
        if kind == "key":
            if isinstance(current, dict) and val in current:
                current = current[val]
                continue
            return None
        if kind == "idx":
            assert isinstance(val, int)
            if isinstance(current, (list, tuple)) and -len(current) <= val < len(
                current
            ):
                current = current[val]
                continue
            return None
        if kind == "idxref":
            assert isinstance(val, str)
            if not isinstance(context, dict):
                return None
            ref_val = lookup_fn(val, context)
            if ref_val is None:
                return None
            if isinstance(current, (list, tuple)):
                if not isinstance(ref_val, int):
                    return None
                if -len(current) <= ref_val < len(current):
                    current = current[ref_val]
                    continue
                return None
            if isinstance(current, dict):
                if isinstance(ref_val, (str, int)) and ref_val in current:
                    current = current[ref_val]
                    continue
                return None
            return None
    return current


class Resolver:
    """Resolves references (!ref), environment variables (${ENV}), and deep keys."""

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}

    def resolve(
        self, value: Any, local_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Recursively resolves markers with support for local scoping.
        """
        # 1. Handle Strings (Interpolation and Tags)
        if isinstance(value, str):
            value = self._interpolate(value)
            if not isinstance(value, str):
                return value

            if value.startswith("!ref:"):
                ref_path = value[5:]
                res = self._resolve_ref(ref_path, local_context)
                # Recurse only if the resolved value is DIFFERENT from the input
                if res != value and isinstance(res, (str, dict)):
                    return self.resolve(res, local_context)
                return res

            if value.startswith("!class:"):
                content = value[7:]
                return self._parse_class_string(content, local_context)

            return value

        # 2. Handle Fluid citizens
        from confluid.fluid import Class, Fluid, Reference

        if isinstance(value, Reference):
            res = self._resolve_ref(value.target, local_context)
            if res == f"!ref:{value.target}":
                return value  # unresolvable — leave the Reference for flow() to retry
            # When the Reference points at another Fluid (Class / Instance /
            # nested Reference), substitute it eagerly so identity-based
            # aliasing works (``result["alias"] is result["thing"]``). When
            # it resolves to a scalar / list / dict, keep the Reference Fluid
            # so later overrides of the source key (e.g. liquifai's
            # ``--drone_index 8`` merged into ``config_data`` after load)
            # can flow through to the rendered value at materialize time.
            if isinstance(res, Fluid):
                return self.resolve(res, local_context)
            return value

        if isinstance(value, (Class, Fluid)):
            return value

        # 3. Handle Dictionary Markers
        if isinstance(value, dict):
            if "_confluid_ref_" in value:
                ref_path = value["_confluid_ref_"]
                # Try local context first, then global
                res = self._resolve_ref(ref_path, local_context)

                # Check for recursion (is the result another marker?)
                if isinstance(res, (dict, str)):
                    return self.resolve(res, local_context)

                # MANDATE: Ensure the resolved value is correctly typed (YAML conversion)
                if isinstance(res, str):
                    return self._parse_primitive(res)
                return res

            if "_confluid_class_" in value:
                # We don't resolve classes here; materialization handles them.
                return value

            # Recurse into normal dicts, passing the current dict as local_context
            return {k: self.resolve(v, local_context=value) for k, v in value.items()}

        # 4. Handle Lists
        if isinstance(value, list):
            return [self.resolve(item, local_context) for item in value]

        return value

    def _parse_class_string(
        self, content: str, local_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Helper to parse 'ClassName(args)' into a marker dict."""
        if "(" in content and content.endswith(")"):
            cls_name, args_str = content[:-1].split("(", 1)
            kwargs = {}
            if args_str.strip():
                for pair in args_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        # Resolve and Parse the value!
                        resolved_v = self.resolve(v, local_context)
                        if isinstance(resolved_v, str):
                            resolved_v = self._parse_primitive(resolved_v)
                        kwargs[k] = resolved_v
            return {"_confluid_class_": cls_name, **kwargs}
        return {"_confluid_class_": content}

    def _resolve_ref(
        self, ref_path: str, local_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Resolve a dotted path against local and global contexts.
        """
        # 1. Try Local Context First
        if local_context:
            val = self._lookup_path(ref_path, local_context)
            if val is not None and (
                not isinstance(val, str) or not val.startswith("!ref:")
            ):
                return val

        # 2. Try Global Context
        val = self._lookup_path(ref_path, self.context)
        if val is not None:
            return val

        logger.warning(f"Failed to resolve reference: {ref_path}")
        return f"!ref:{ref_path}"

    def _lookup_path(self, path: str, context: Dict[str, Any]) -> Any:
        """Drill into a dict / list via a dotted + bracketed path.

        Supported segment shapes:

        * Dotted dict keys — ``a.b.c``.
        * List indices (numeric segment after dot) — ``items.0``.
        * List indices (bracket form, ``int`` literal) — ``items[0]`` /
          ``items[-1]``.
        * Bracketed name refs — ``items[idx]``: resolves ``idx`` against the
          same context, then uses the resolved value as a list index (when
          ``int``) or dict key (when ``str`` / ``int``). The inner name may
          itself be a bracketed / dotted path (``packs[config.which]``).
        * Free combinations — ``packs[0].name``, ``packs[idx].sub[1]``.

        Returns ``None`` when the path can't be fully walked, which the
        caller treats as "unresolved" — same as before.
        """
        # 1. Direct literal lookup first — preserves the legacy "keys with
        #    dots / brackets in the literal name" behavior.
        if path in context:
            return context[path]

        # 2. Tokenize, then walk.
        segments = _parse_path_segments(path)
        if segments is None:
            return None
        return _walk_path_segments(segments, context, self._lookup_path)

    def _interpolate(self, value: str) -> Any:
        env_pattern = r"\$\{([\w_]+)(?::([^}]+))?\}"

        def env_replacer(match: re.Match) -> str:
            var_name = match.group(1)
            default_val = match.group(2)
            return os.getenv(var_name, default_val or match.group(0))

        if "${" in value:
            match = re.fullmatch(env_pattern, value)
            if match:
                var_name = match.group(1)
                default_val = match.group(2)
                env_val = os.getenv(var_name)
                if env_val is not None:
                    return self._parse_primitive(env_val)
                return (
                    self._parse_primitive(default_val)
                    if default_val is not None
                    else value
                )

            value = re.sub(env_pattern, env_replacer, value)

        return value

    def _parse_primitive(self, value: str) -> Any:
        """Convert string to appropriate Python primitive."""
        if value.startswith("!ref:"):
            return value
        return parse_value(value)


def parse_value(value: str) -> Any:
    """Parse a string value into a Python type using YAML for complex types.

    Examples:
        "42" -> 42, "3.14" -> 3.14, "true" -> True, "[1, 2]" -> [1, 2]
    """
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None

    try:
        return yaml.safe_load(value)
    except Exception:
        return value
