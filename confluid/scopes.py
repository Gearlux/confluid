from copy import deepcopy
from typing import Any, Dict, List, Set

from logflow import get_logger

from confluid.merger import deep_merge
from confluid.registry import get_registry

logger = get_logger("confluid.scopes")
registry = get_registry()


def resolve_scopes(config: Dict[str, Any], active_scopes: List[str]) -> Dict[str, Any]:
    """
    Apply scoped overlays to the base configuration.
    """
    logger.debug(f"Resolving scopes: {active_scopes}")

    # 1. Resolve aliases
    aliases = config.get("scope_aliases", {})
    resolved_scopes = _resolve_aliases(active_scopes, aliases)

    # 2. Re-expand scope list to include full hierarchy
    all_active: Set[str] = set()
    for s in resolved_scopes:
        all_active.update(_expand_hierarchy(s))

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
                    merged = deep_merge(merged, merged[key])

    # 5. Apply positive scopes in order
    for scope_name in resolved_scopes:
        hierarchy = _expand_hierarchy(scope_name)
        for s in hierarchy:
            if s in merged and isinstance(merged[s], dict):
                merged = deep_merge(merged, merged[s])

    # 6. Cleanup: remove all identified scope metadata
    for key in scope_keys:
        merged.pop(key, None)

    # Remove metadata keys
    merged.pop("scope_aliases", None)
    merged.pop("scopes", None)

    return merged


def _resolve_aliases(requested: List[str], aliases: Dict[str, Any]) -> List[str]:
    """Recursively expand aliases in the requested scopes."""
    resolved: List[str] = []
    seen: Set[str] = set()

    def expand(name: str, path: List[str]) -> None:
        if name in path:
            raise ValueError(f"Circular scope alias detected: {' -> '.join(path + [name])}")

        if name in seen:
            return
        seen.add(name)

        if name in aliases:
            target = aliases[name]
            new_path = path + [name]
            if isinstance(target, str):
                expand(target, new_path)
            elif isinstance(target, list):
                for t in target:
                    expand(t, new_path)
        else:
            resolved.append(name)

    for r in requested:
        seen.clear()
        expand(r, [])

    return resolved


def _expand_hierarchy(scope_name: str) -> List[str]:
    parts = scope_name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]
