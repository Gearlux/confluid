"""``confluid.__version__`` is the installed distribution's version, read from its metadata.

The version is written ONCE, in ``pyproject.toml``. A typed-in ``__version__ = "..."`` is a second
copy that drifts: before this rule three workspace packages reported ``0.2.0`` while their
``pyproject.toml`` said ``0.1.0``.
"""

from importlib.metadata import version

import confluid


def test_version_is_the_installed_distribution_version() -> None:
    assert confluid.__version__ == version("confluid")
