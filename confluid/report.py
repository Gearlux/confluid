"""Configuration reporting (``ConfigurationReport`` — applied / failed / unused keys).

A dependency LEAF module (stdlib + loggair only, like ``fluid``): both the
engine (YAML materialization) and the configurator (post-construction
``configure()``) import it without creating a layering cycle.

One :class:`ConfigurationReport` spans one configuration pass — a whole
``configure(*instances, ...)`` call, or everything inside a
``confluid.collect_report()`` block (``load()`` / ``materialize()`` /
``flow()`` plus any nested ``configure()``, which adopts the ambient report).
Three buckets:

* **applied** — every override that landed on an object, with the receiver
  label and the origin that delivered it (bare broadcast, named block, glob,
  addressed recursion, nested-class broadcast). Last-write-wins collapses to
  ONE record per attribute per object — the final effective assignment.
* **failed** — deliberately small, ``configure()`` path only: a typo'd
  non-dict key inside an object's own named block (``"unknown-attribute"``)
  and per-field validation failures (``"validation"``; strict mode records
  then re-raises, warn mode records with the value still applied). The
  engine path does NOT record failures: ``validate_kwargs`` fires inside the
  wrapped ``__init__`` (validation sits below the engine in the module
  layering) and strict mode already raises located ``ConstructionError``s.
* **unused** — candidate top-level document keys that matched NOTHING across
  the whole pass. Candidates are registered explicitly
  (:meth:`ConfigurationReport.add_config_keys`) and :meth:`mark_used` is a
  no-op for unregistered names, so hoisted routing metadata (EXACT/STRICT
  view entries) can never false-positive.

The report is a plain MUTABLE accumulator — it rides inside the frozen
``_EngineState`` exactly like the mutable ``flow_memo`` dicts do. All
instrumentation sites are ``if report is not None``-guarded so the default
(no report active) path stays zero-cost.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from loggair import get_logger

logger = get_logger("confluid.report")


@dataclass(frozen=True)
class AppliedKey:
    """One effective (last-write-wins) override applied to one object."""

    key: str  #: attribute / config key as applied (post dotted-key expansion)
    target: str  #: receiver label — ``"Trainer"`` or ``"Trainer 'encoder'"``
    #: ``"bare"`` | ``"block 'X'"`` | ``"glob '**'"`` | ``"glob '*'"`` | ``"addressed"`` | ``"nested-class"``
    origin: str
    note: Optional[str] = None  #: e.g. the eager-class staleness note


@dataclass(frozen=True)
class FailedKey:
    """One override that could not (fully) apply."""

    key: str
    target: str
    reason: str  #: ``"unknown-attribute"`` | ``"validation"``
    detail: Optional[str] = None  #: validation error text, when available


class ConfigurationReport:
    """Mutable accumulator for one configuration pass.

    Built and returned by :func:`confluid.configure` /
    :func:`confluid.configure_from_file`, and installed on the engine state by
    :func:`confluid.collect_report` so the YAML materialization path
    (``load`` / ``materialize`` / ``flow``) reports into it too.
    """

    def __init__(self) -> None:
        self.applied: List[AppliedKey] = []
        self.failed: List[FailedKey] = []
        # Insertion-ordered candidate top-level keys -> used flag. Only keys
        # registered here can ever appear in ``unused`` — ``mark_used`` on any
        # other name (hoisted routing metadata, block-internal keys) is a no-op.
        self._config_keys: Dict[str, bool] = {}

    def record_applied(self, key: str, target: str, origin: str, note: Optional[str] = None) -> None:
        self.applied.append(AppliedKey(key=key, target=target, origin=origin, note=note))

    def record_failed(self, key: str, target: str, reason: str, detail: Optional[str] = None) -> None:
        self.failed.append(FailedKey(key=key, target=target, reason=reason, detail=detail))

    def add_config_keys(self, keys: Iterable[str]) -> None:
        """Register candidate document keys (idempotent — a used flag survives)."""
        for key in keys:
            self._config_keys.setdefault(key, False)

    def mark_used(self, key: str) -> None:
        """Flag a registered candidate as used; unregistered names are ignored."""
        if key in self._config_keys:
            self._config_keys[key] = True

    @property
    def unused(self) -> List[str]:
        """Registered document keys that matched nothing, in document order."""
        return [k for k, used in self._config_keys.items() if not used]

    def summary(self) -> str:
        return f"{len(self.applied)} applied, {len(self.failed)} failed, {len(self.unused)} unused"

    def log_unused(self) -> None:
        """Emit the ONE aggregate DEBUG line for unused keys (silent when none).

        DEBUG, not warning: a bare key legitimately matches only some nodes,
        and an override document may target instances configured in a later
        pass — an unmatched key is diagnostic, not actionable per se.
        """
        unused = self.unused
        if unused:
            logger.debug(f"configuration report: {len(unused)} unused key(s): {', '.join(unused)}")

    def __repr__(self) -> str:
        return f"<ConfigurationReport applied={len(self.applied)} failed={len(self.failed)} unused={len(self.unused)}>"
