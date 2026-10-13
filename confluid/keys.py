"""The reserved-key vocabulary — the key names that turn a plain YAML mapping into a marker.

``_target_`` / ``_partial_`` mirror Hydra's vocabulary; ``_ref_`` / ``_scope_`` / ``_notscope_`` are confluid's
additions for the constructs Hydra has no equivalent for. A document written this way is ORDINARY YAML —
``yaml.safe_load`` and external tooling (yq, editor schemas) read it, which no tagged document can be. Both spellings
produce the SAME Fluid markers, so every downstream pass (includes, scopes, interpolation, broadcasting, flow) is
untouched by which one an author used.

This module imports nothing from confluid, on purpose: the loader (which parses the keys), the resolver (which names
them in a refusal) and the dumper (which writes them) all take the names from here at the top of the file, so none
of them reaches into another for a string (``docs/architecture.md`` record 30).
"""

from typing import FrozenSet

TARGET_KEY = "_target_"
PARTIAL_KEY = "_partial_"
REF_KEY = "_ref_"
#: REMOVED 2026-08-15 (zero users; architecture record 18). Kept in
#: RESERVED_KEYS on purpose: a mapping carrying it must still reach the marker gate
#: so it can be REFUSED with a location, instead of loading silently as plain data
#: with a literal ``_clone_`` key — the degradation this format exists to end.
_REMOVED_CLONE_KEY = "_clone_"
SCOPE_KEY = "_scope_"
NOTSCOPE_KEY = "_notscope_"

#: Every key that turns a plain mapping into a marker. A mapping carrying NONE of
#: these is an ordinary dict and takes PyYAML's untouched construction path.
RESERVED_KEYS: FrozenSet[str] = frozenset(
    {TARGET_KEY, PARTIAL_KEY, REF_KEY, _REMOVED_CLONE_KEY, SCOPE_KEY, NOTSCOPE_KEY}
)
