"""Configuration reporting (``ConfigurationReport`` — applied / failed / unused keys).

A dependency LEAF module (stdlib + loggair only, like ``fluid``): both the
engine (YAML materialization) and the configurator (post-construction
``configure()``) import it without creating a layering cycle.

One :class:`ConfigurationReport` spans one configuration pass — a whole
``configure(*instances, ...)`` call, or everything inside a
``confluid.collect_report()`` block (``load()`` /
``flow()`` plus any nested ``configure()``, which adopts the ambient report).
Three buckets:

* **applied** — every override that landed on an object, with the receiver
  label and the origin that delivered it (bare broadcast, named block, glob,
  addressed recursion, nested-class broadcast). Last-write-wins collapses to
  ONE record per attribute per object — the final effective assignment.
* **failed** — deliberately small. An ADDRESSED key naming nothing the target
  declares records ``"unknown-attribute"`` (B1, 2026-08-12 —
  ``engine._warn_undeclared`` and ``_MergeSink.unknown``; the own-kwarg form still
  APPLIES the value, warning as it does). Per-field validation failures
  (``"validation"``) stay ``configure()``-only: on the load path
  ``validate_kwargs`` fires inside the wrapped ``__init__`` (below the engine
  in the layering) and strict mode already raises located
  ``ConstructionError``s.
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from loggair import get_logger

logger = get_logger("confluid.report")


def _short_repr(value: Any, limit: int = 48) -> str:
    """A bounded, display-safe rendering of a config value — always a STRING.

    The contest ledger below must never hold the VALUE: a config value is an
    arbitrary object (a dataset, a model, an array), and keeping one alive for
    the report's lifetime would turn a diagnostic into a memory leak. It also
    must never raise — an object's ``__repr__`` is user code.
    """
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - a diagnostic must not break the pass it describes
        return f"<{type(value).__name__}>"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


@dataclass(frozen=True)
class Candidate:
    """One document source that competed to set a key on one object.

    Confluid's ONE precedence rule is document position, last spec wins — so
    "which value won" is only half an answer, and the half nobody has to ask
    about. :attr:`pos` is what explains the other half.
    """

    origin: str  #: same vocabulary as :attr:`AppliedKey.origin`
    value: str  #: a bounded repr (see :func:`_short_repr`) — never the object
    pos: int  #: index in the receiver's visible document view; higher wins


@dataclass(frozen=True)
class AppliedKey:
    """One effective (last-write-wins) override applied to one object."""

    key: str  #: attribute / config key as applied (post dotted-key expansion)
    target: str  #: receiver label — ``"Trainer"`` or ``"Trainer 'encoder'"``
    #: ``"bare"`` | ``"block 'X'"`` | ``"glob '**'"`` | ``"glob '*'"`` | ``"addressed"`` |
    #: ``"nested-class"`` | ``"deferred slot"`` | ``"own"``
    origin: str
    note: Optional[str] = None  #: e.g. the eager-class staleness note
    #: Every source that competed for this key on this object, in DOCUMENT
    #: ORDER — the last entry is the one that won. Empty for the paths that do
    #: not scan a view (the deferred-slot cascade, a direct ``flow()``), where
    #: there is no ordering to report. Read it through :meth:`explain`.
    contest: Tuple[Candidate, ...] = ()


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

    def record_applied(
        self,
        key: str,
        target: str,
        origin: str,
        note: Optional[str] = None,
        candidates: Sequence[Tuple[str, Any, int]] = (),
    ) -> None:
        """Record one effective override, and the contest it won if there WAS one.

        ``candidates`` is the scanner's raw ``(origin, value, pos)`` stream for this
        key, in document order. It is converted to :class:`Candidate` here — and
        ONLY when something actually competed:

        * a single candidate is not a contest. It says nothing ``origin`` does not
          already say, and rendering it costs a ``repr()`` per applied key, which
          measured as 4.6 ms of the 5.7 ms this ledger added to a 2,500-marker
          ``configure()`` pass — 80 % of the cost, for the case that never needed
          explaining;
        * two or more is the case ``explain`` exists for, and there the ``repr``
          is what makes the answer readable.

        The raw values are dropped here and never stored: the report outlives the
        pass, and a config value may be a dataset or a model.
        """
        self.applied.append(
            AppliedKey(
                key=key,
                target=target,
                origin=origin,
                note=note,
                contest=(
                    tuple(Candidate(o, _short_repr(v), p) for o, v, p in candidates) if len(candidates) > 1 else ()
                ),
            )
        )

    def record_failed(self, key: str, target: str, reason: str, detail: Optional[str] = None) -> None:
        self.failed.append(FailedKey(key=key, target=target, reason=reason, detail=detail))

    def add_config_keys(self, keys: Iterable[str]) -> None:
        """Register candidate document keys (idempotent — a used flag survives)."""
        for key in keys:
            self._config_keys.setdefault(key, False)

    def mark_used(self, key: str) -> None:
        """Flag a registered candidate as used; unregistered names are ignored.

        A LEAF key also satisfies its glob-registered spellings (``**.lr`` /
        ``*.lr``): a glob block registers per leaf under the glob prefix, but
        the nested-marker cascade delivers from a POOL in which rider contents
        are indistinguishable from bare keys (``_broadcast_pool`` flattens both
        to BARE), so that path can only ever mark the leaf — and a rider whose
        content reached every slot it aimed at still reported ``**.lr`` unused
        (measured through a consumer's CLI-override pipeline: ``--**.lr`` with
        ``applied=[('lr', 'nested-class'), …]`` and ``unused=['**.lr']`` in one
        report). Satisfying the glob spelling from the leaf matches the
        mechanism's real granularity: bare and rider deliveries of one key are
        fused at the pool, so the report cannot tell them apart either.
        """
        if key in self._config_keys:
            self._config_keys[key] = True
        for glob_spelling in (f"**.{key}", f"*.{key}"):
            if glob_spelling in self._config_keys:
                self._config_keys[glob_spelling] = True

    @property
    def unused(self) -> List[str]:
        """Registered document keys that matched nothing, in document order."""
        return [k for k, used in self._config_keys.items() if not used]

    def summary(self) -> str:
        return f"{len(self.applied)} applied, {len(self.failed)} failed, {len(self.unused)} unused"

    def explain(self, key: str, target: Optional[str] = None) -> str:
        """Why ``key`` ended up with the value it has — the contest, in document order.

        Confluid arbitrates by POSITION and nothing else, which is the one thing
        about it that surprises people: a value written at a node can lose to a
        bare key simply because the bare key sits lower in the file. That is the
        documented rule (docs/broadcasting.md), and until this method existed the
        only way to see it happen was ``LOGGAIR_CONSOLE_LEVEL=TRACE`` and a grep.

        ::

            with collect_report() as report:
                cfg = load("experiment.yaml")
            print(report.explain("lr"))

            lr on Trainer = 0.9
              ✓ #4  bare              0.9    applied
                #1  block 'Trainer'   0.5    beaten — earlier in the document

        Pass ``target`` to narrow to one receiver when several got the same key.
        A key with no recorded contest still prints its applied line — nothing
        competed for it, so there is nothing to rank. That covers both the common
        case (one source wrote it) and the paths with no view to order at all
        (the deferred-slot cascade, a direct ``flow()``), which is why a single
        candidate is not stored: it says nothing ``origin`` does not.

        A key whose winning write is the marker's OWN kwarg is a definition, not
        an override, and is not recorded as applied — so it has no line here. It
        still appears as a losing candidate when a later key beats it, which is
        the case worth explaining.
        """
        rows = [a for a in self.applied if a.key == key and (target is None or a.target == target)]
        if not rows:
            known = sorted({a.key for a in self.applied})
            listed = ", ".join(known) if known else "(nothing was applied in this pass)"
            scope = f" on {target!r}" if target is not None else ""
            # "Not applied" is not the same as "not set", and conflating them sends
            # the reader hunting for a bug that isn't there: a marker's own kwarg
            # and a constructor default both produce a value that nothing overrode,
            # which is precisely what having no override record means.
            return (
                f"{key!r} overrode nothing{scope} — it is either unset, or set by a source that is "
                f"not an override (the marker's own kwarg, or the constructor default). "
                f"Overridden keys: {listed}"
            )

        lines: List[str] = []
        for entry in rows:
            winner = entry.contest[-1] if entry.contest else None
            head = f"{entry.key} on {entry.target}"
            lines.append(f"{head} = {winner.value}" if winner is not None else head)
            if not entry.contest:
                lines.append(f"    via {entry.origin} — nothing else competed for it")
            for candidate in entry.contest:
                won = candidate is winner
                mark, why = ("✓", "applied") if won else (" ", "beaten — earlier in the document")
                lines.append(f"  {mark} #{candidate.pos:<3} {candidate.origin:<18} {candidate.value:<12} {why}")
            if entry.note:
                lines.append(f"    note: {entry.note}")
        return "\n".join(lines)

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
