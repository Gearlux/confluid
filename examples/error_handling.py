"""Typed exceptions — companion to ``docs/errors.md``.

Triggers the common failure modes and shows that every confluid exception is
rooted at ``ConfluidError`` AND dual-inherits the builtin it replaces, so
pre-existing ``except ValueError:`` / ``except FileNotFoundError:`` code keeps working.
"""

import confluid


def main() -> None:
    # 1. Missing config file -> ConfigFileNotFoundError, also a FileNotFoundError.
    try:
        confluid.load_config("/nonexistent/experiment.yaml")
    except confluid.ConfigFileNotFoundError as exc:
        assert isinstance(exc, FileNotFoundError), "dual-inherits the builtin"
        print(f"missing file -> {type(exc).__name__} (also FileNotFoundError)")
    else:
        raise AssertionError("expected ConfigFileNotFoundError")

    # 2. Unknown !class: target -> UnknownClassError, also a ValueError.
    try:
        confluid.load("model: !class:NoSuchThing()")
    except confluid.UnknownClassError as exc:
        assert isinstance(exc, ValueError)
        print(f"unknown class -> {type(exc).__name__} (also ValueError)")
    else:
        raise AssertionError("expected UnknownClassError")

    # 3. Everything roots at ConfluidError, so one except catches any confluid failure.
    try:
        confluid.load("model: !class:StillMissing()")
    except confluid.ConfluidError as exc:
        print(f"the root ConfluidError catches it too: {type(exc).__name__}")

    # 4. ConfigurationError is the base for all config-CONTENT errors.
    assert issubclass(confluid.UnknownClassError, confluid.ConfigurationError)
    assert issubclass(confluid.ReferenceResolutionError, confluid.ConfigurationError)
    assert issubclass(confluid.CircularIncludeError, confluid.ConfigurationError)
    print("UnknownClassError / ReferenceResolutionError / CircularIncludeError all extend ConfigurationError")


if __name__ == "__main__":
    main()
