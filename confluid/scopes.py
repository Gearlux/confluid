from copy import deepcopy
from typing import Any, Dict, List, Set

from confluid.merger import deep_merge


def resolve_scopes(config: Dict[str, Any], active_scopes: List[str]) -> Dict[str, Any]:
    """
    Apply scoped overlays to the base configuration.
    Priority: Negative Scopes -> Hierarchy (Parent to Child) -> Explicit Scopes (Later overrides earlier).
    """
    # 1. Resolve aliases
    resolved_scopes = _resolve_aliases(active_scopes, config.get("scope_aliases", {}))

    # 2. Expand hierarchy (e.g. prod.gpu -> [prod, prod.gpu])
    all_active: Set[str] = set()
    for s in resolved_scopes:
        parts = s.split(".")
        for i in range(len(parts)):
            all_active.add(".".join(parts[: i + 1]))

    # Work on a copy
    merged = deepcopy(config)

    # 3. Apply negative scopes (e.g. 'not debug')
    for key in list(merged.keys()):
        if key.startswith("not ") and len(key) > 4:
            target_scope = key[4:]
            if target_scope not in all_active:
                if isinstance(merged[key], dict):
                    deep_merge(merged, merged[key])
            merged.pop(key)

    # 4. Apply positive scopes in order
    for scope_name in resolved_scopes:
        hierarchy = _expand_hierarchy(scope_name)
        for s in hierarchy:
            if s in merged and isinstance(merged[s], dict):
                deep_merge(merged, merged[s])
            # Note: We don't pop yet to allow nested inheritance

    # 5. Cleanup: remove all scope definitions and aliases
    for key in list(merged.keys()):
        # Remove anything that looks like a scope (positive, negative, or metadata)
        if key == "scopes" or key == "scope_aliases" or key.startswith("not "):
            merged.pop(key, None)
        elif _is_known_scope(key, config):
            merged.pop(key, None)

    return merged


def _resolve_aliases(requested: List[str], aliases: Dict[str, Any]) -> List[str]:
    """Expand scope aliases recursively."""
    resolved: List[str] = []
    seen: Set[str] = set()

    def expand(name: str) -> None:
        if name in seen:
            raise ValueError(f"Circular scope alias detected: {name}")
        seen.add(name)

        if name in aliases:
            targets = aliases[name]
            if isinstance(targets, str):
                targets = [s.strip() for s in targets.split(",")]
            for t in targets:
                expand(t)

        resolved.append(name)

    for r in requested:
        seen.clear()
        expand(r)

    return resolved


def _expand_hierarchy(scope_name: str) -> List[str]:
    parts = scope_name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _is_known_scope(key: str, config: Dict[str, Any]) -> bool:
    """Check if a key represents a defined scope or alias."""
    if key in config.get("scope_aliases", {}):
        return True
    # If the key contains a dot or is a top-level dict that is also found in aliases
    # This is a heuristic for cleaning up the final dict
    return "." in key
