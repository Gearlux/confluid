"""PEP 563 (`from __future__ import annotations`) fixtures for the I5 pins.

A separate module because the behaviour under test is module-level: the flag
turns every annotation in THIS file into a string, and `get_type_hints` then
resolves them against this module's globals — where `Decimal` deliberately does
not exist at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from confluid import configurable
from confluid.partial import Partial

if TYPE_CHECKING:  # never imported at runtime — the whole point
    from decimal import Decimal


@configurable(validate=False)
class Pipe:
    """One unresolvable hint (`precision`) beside two perfectly good ones."""

    def __init__(
        self,
        precision: Optional[Decimal] = None,
        stages: Optional[List[int]] = None,
        optimizer: Optional[Partial[object]] = None,
    ) -> None:
        self.precision = precision
        self.stages = stages
        self.optimizer = optimizer


@configurable(validate=False)
class PipeClean:
    """The SAME class with the unresolvable parameter removed."""

    def __init__(
        self,
        stages: Optional[List[int]] = None,
        optimizer: Optional[Partial[object]] = None,
    ) -> None:
        self.stages = stages
        self.optimizer = optimizer


@configurable(validate=False)
class PipeBodySlots:
    """The body-slot half of the same problem."""

    def __init__(self) -> None:
        self.precision: Optional[Decimal] = None
        self.stages: Optional[List[int]] = None
        self.optimizer: Optional[Partial[object]] = None
