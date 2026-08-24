"""Typed exceptions — companion to ``docs/errors.md``.

Triggers the common failure modes and shows that every confluid exception is
rooted at ``ConfluidError`` AND dual-inherits the builtin it replaces, so
pre-existing ``except ValueError:`` / ``except FileNotFoundError:`` code keeps working.
"""

import confluid


def main() -> None:
    # 1. Missing config file -> ConfigFileNotFoundError, also a FileNotFoundError.
    try:
        confluid.load("/nonexistent/experiment.yaml", until="raw")
    except confluid.ConfigFileNotFoundError as exc:
        assert isinstance(exc, FileNotFoundError), "dual-inherits the builtin"
        print(f"missing file -> {type(exc).__name__} (also FileNotFoundError)")
    else:
        raise AssertionError("expected ConfigFileNotFoundError")

    # 2. Unknown target -> UnknownClassError, also a ValueError.
    try:
        confluid.load("model: !class:NoSuchThing")
    except confluid.UnknownClassError as exc:
        assert isinstance(exc, ValueError)
        print(f"unknown class -> {type(exc).__name__} (also ValueError)")
    else:
        raise AssertionError("expected UnknownClassError")

    # 3. Everything roots at ConfluidError, so one except catches any confluid failure.
    try:
        confluid.load("model: !class:StillMissing")
    except confluid.ConfluidError as exc:
        print(f"the root ConfluidError catches it too: {type(exc).__name__}")

    # 4. Two classes may share a NAME when a discovery tag tells them apart — so a bare
    #    reference to that name is a question with two answers, and confluid says so
    #    instead of binding whichever module imported last.
    @confluid.configurable(category="op", group="fft/numpy")
    class FourierOp:
        pass

    def _torch_variant() -> type:
        @confluid.configurable(category="op", group="fft/torch")
        class FourierOp:  # same name, different group — a legal pair
            pass

        return FourierOp

    torch_op = _torch_variant()
    try:
        confluid.load("op: !class:FourierOp")
    except confluid.AmbiguousClassError as exc:
        assert isinstance(exc, confluid.ConfigurationError)
        print(f"shared name -> {type(exc).__name__} listing {len(str(exc).splitlines()) - 2} candidates")
    else:
        raise AssertionError("expected AmbiguousClassError")

    # ...and each of the three ways to choose one:
    # The `@axis=value` selector is part of the target NAME, not tag syntax, so it
    # rides an ordinary target string unchanged.
    picked = confluid.load("op: !class:FourierOp@group=fft/torch")["op"]
    assert isinstance(picked, torch_op), "the target selector picks the torch variant"
    assert confluid.get_registry().get_class("FourierOp", group="fft/numpy") is FourierOp
    print("selector / filter / dotted path each resolve one class")

    # 5. ConfigurationError is the base for all config-CONTENT errors.
    assert issubclass(confluid.UnknownClassError, confluid.ConfigurationError)
    assert issubclass(confluid.AmbiguousClassError, confluid.ConfigurationError)
    assert issubclass(confluid.ReferenceResolutionError, confluid.ConfigurationError)
    assert issubclass(confluid.CircularIncludeError, confluid.ConfigurationError)
    print("Unknown/Ambiguous/ReferenceResolution/CircularInclude all extend ConfigurationError")


if __name__ == "__main__":
    main()
