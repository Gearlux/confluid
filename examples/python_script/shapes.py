"""The classes the example writes a script for — in a module of their own, because a script imports each class by its
module and a class defined in the script being run (``__main__``) has no module another script could import."""

from dataclasses import dataclass, field
from typing import List, Optional

from confluid import configurable


@configurable
@dataclass(kw_only=True)
class Tone:
    frequency_hz: float = 1000.0
    level_db: float = 0.0


@configurable
@dataclass(kw_only=True)
class Mixture:
    tones: List[Tone] = field(default_factory=list)
    duration_s: float = 0.01
    reference: Optional[Tone] = None
