from copy import deepcopy
from typing import Any, Dict, List, Set

from confluid.merger import deep_merge


def resolve_scopes(config: Dict[str, Any], active_scopes: List[str]) -> Dict[str, Any]:
    """
    Apply scoped overlays to the base configuration.
    """
    from confluid.registry import get_registry

    registry = get_registry()

    # 1. Resolve aliases
    aliases = config.get("scope_aliases", {})
    resolved_scopes = _resolve_aliases(active_scopes, aliases)

    # 2. Expand hierarchy (e.g. prod.gpu -> [prod, prod.gpu])
    all_active: Set[str] = set()
    for s in resolved_scopes:
        parts = s.split(".")
        for i in range(len(parts)):
            all_active.add(".".join(parts[: i + 1]))

    # Work on a copy
    merged = deepcopy(config)

    # 3. Identify all keys that represent scopes or aliases for later cleanup
    scope_keys = set(aliases.keys())
    for key in merged:
        if key.startswith("not ") or "." in key:
            scope_keys.add(key)
        elif isinstance(merged[key], dict) and not registry.get_class(key):
            # Key is a dict but NOT a registered class -> likely a scope
            scope_keys.add(key)

    # 4. Apply negative scopes (e.g. 'not prod')
    for key in list(merged.keys()):
        if key.startswith("not ") and len(key) > 4:
            target_scope = key[4:]
            if target_scope not in all_active:
                if isinstance(merged[key], dict):
                    deep_merge(merged, merged[key])

    # 5. Apply positive scopes in order
    for scope_name in resolved_scopes:
        hierarchy = _expand_hierarchy(scope_name)
        for s in hierarchy:
            if s in merged and isinstance(merged[s], dict):
                deep_merge(merged, merged[s])

    # 6. Cleanup: remove all identified scope metadata
    for key in scope_keys:
        merged.pop(key, None)

    # Remove metadata keys
    merged.pop("scope_aliases", None)
    merged.pop("scopes", None)

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
