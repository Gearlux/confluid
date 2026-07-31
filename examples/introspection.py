"""Introspection without cost — companion to ``docs/introspection.md``.

Contrasts ``resolve()`` (pure Fluid markers, nothing constructed) with
``materialize(..., solidify=False)`` (live-but-inert objects), narrows a node
with ``cast``, and closes with a ``dump`` -> ``load`` round-trip.
"""

from typing import Optional

from confluid import Instance, cast, configurable, dump, load, materialize, resolve


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
backbone: !class:Backbone(depth=101)
"""


def main() -> None:
    # (a) resolve(): broadcast-resolved MARKERS — nothing is instantiated.
    markers = resolve(load_config_text(DOC))
    assert isinstance(markers["backbone"], Instance), "still a Fluid marker, not a live object"
    print(f"resolve(): backbone stays a {type(markers['backbone']).__name__} marker")

    # (b) solidify=False: constructed (cheap) but the expensive solidify() is suppressed.
    inert = materialize(load_config_text(DOC), solidify=False)
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


def load_config_text(text: str) -> dict:
    """Parse the tagged YAML into the dict form ``resolve``/``materialize`` consume."""
    import yaml

    from confluid.loader import ConfluidLoader

    data: dict = yaml.load(text, Loader=ConfluidLoader)  # nosec: confluid's own SafeLoader subclass
    return data


if __name__ == "__main__":
    main()
