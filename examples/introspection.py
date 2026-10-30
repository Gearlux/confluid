"""Introspection without cost — companion to ``docs/introspection.md``.

Contrasts ``load(until="settled")`` (pure Fluid markers, nothing constructed) with
``load(..., solidify=False)`` (live-but-inert objects), narrows a node
with ``cast``, and closes with a ``dump`` -> ``load`` round-trip.
"""

import tempfile
from pathlib import Path
from typing import Optional

from confluid import Target, cast, configurable, document_notes, dump, load


@configurable
class Backbone:
    def __init__(self, depth: int = 50) -> None:
        """A model backbone whose expensive build is deferred to solidify().

        Args:
            depth: Number of layers.
        """
        self.depth = depth
        self.built: Optional[str] = None

    def solidify(self) -> None:
        """The expensive post-flow step (imagine weights downloading here)."""
        self.built = f"resnet{self.depth}"


DOC = """
backbone: !class:Backbone
  depth: 101
"""

#: A config whose settings carry comments: the help a command shows for them.
NOTED = """\
# How deep the backbone goes.
depth: 50
# a smaller backbone
small: !scope:small
  depth: 18

backbone: !class:Backbone
  depth: !ref:depth
"""


def main() -> None:
    # (a) load(until="settled"): broadcast-resolved MARKERS — nothing is instantiated.
    markers = load(load_config_text(DOC), until="settled")
    assert isinstance(markers["backbone"], Target), "still a Fluid marker, not a live object"
    print(f"load(until='settled'): backbone stays a {type(markers['backbone']).__name__} marker")

    # (b) solidify=False: constructed (cheap) but the expensive solidify() is suppressed.
    inert = load(load_config_text(DOC), solidify=False)
    assert isinstance(inert["backbone"], Backbone) and inert["backbone"].built is None
    print(f"solidify=False: live Backbone, built={inert['backbone'].built}")

    # Default load(): fully solid — solidify() ran.
    solid = load(DOC)
    assert solid["backbone"].built == "resnet101"
    print(f"default load(): built={solid['backbone'].built}")

    # cast(): flow + a type assertion for mypy/IDEs.
    backbone = cast(markers["backbone"], Backbone)
    assert backbone.depth == 101  # <- type-checked attribute access
    print(f"cast() narrowed the marker to Backbone(depth={backbone.depth})")

    # dump() -> load(): full-fidelity round-trip.
    reconstructed = load(dump(backbone))
    assert isinstance(reconstructed, Backbone) and reconstructed.depth == 101
    print("dump() -> load() reconstructed an identical Backbone")

    # document_notes(): a config's own settings with their comments, the parameters each feeds, its scopes.
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "run.yaml"
        config.write_text(NOTED)
        notes = document_notes(config)
    assert [(key.name, key.comment) for key in notes.keys] == [
        ("depth", "How deep the backbone goes."),
        ("backbone", ""),
    ]
    assert notes.keys[0].feeds == (("Backbone", "depth"),)
    assert [(scope.activation, scope.comment) for scope in notes.scopes] == [("small", "a smaller backbone")]
    print(f"document_notes(): {[key.name for key in notes.keys]} and the scope {notes.scopes[0].activation!r}")


def load_config_text(text: str) -> dict:
    """Parse the YAML into the dict form ``load`` consumes."""
    import yaml

    from confluid.loader import ConfluidLoader

    data: dict = yaml.load(text, Loader=ConfluidLoader)  # nosec: confluid's own SafeLoader subclass
    return data


if __name__ == "__main__":
    main()
