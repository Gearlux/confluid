"""``pkg://<distribution>/<path>`` — a config file named by the installed distribution that ships it.

A config that includes another project's config by a relative path (``../../other-project/config/a.yaml``) only
works where both checkouts sit side by side. ``pkg://other-project/config/a.yaml`` names the file by the
distribution that ships it — the name ``pip install`` takes — and its path inside that project's folder, and reads
it wherever the distribution is installed:

* an EDITABLE install (``pip install -e``, PEP 660) records its checkout in ``direct_url.json`` (PEP 610): the file is
  read from the checkout, so editing it there takes effect at once;
* any other install is a wheel, whose ``RECORD`` lists every file it installed: a config ships at
  ``share/<distribution>/<path>`` under the install prefix (the wheel's ``data`` scheme), ``<distribution>`` in its
  normalized form (PEP 503: ``my-project-5g``).

Only the named distribution's own files are searched, so ``pkg://my-project/...`` never finds a file of
``my-project-5g``. A miss is a :class:`~confluid.exceptions.ConfigFileNotFoundError` naming the distribution, its
version and what is missing; a malformed reference is a :class:`~confluid.exceptions.ConfigurationError`. Docs:
``docs/search-paths.md`` → "Configs shipped by an installed distribution"; the decision: ``docs/architecture.md`` §33.
"""

import json
import re
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import NoReturn, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import url2pathname

from confluid.exceptions import ConfigFileNotFoundError, ConfigurationError

#: The prefix that makes a config path a package reference.
PACKAGE_SCHEME = "pkg://"
#: The folder under the install prefix a wheel ships its configs in: ``share/<distribution>/<path>``.
SHARE_DIR = "share"

__all__ = ["PACKAGE_SCHEME", "SHARE_DIR", "canonical_name", "is_package_reference", "resolve_package_reference"]


def is_package_reference(value: object) -> bool:
    """Is ``value`` a ``pkg://`` reference? Only a ``str`` can be — a ``Path`` folds the ``//`` away."""
    return isinstance(value, str) and value.startswith(PACKAGE_SCHEME)


def canonical_name(name: str) -> str:
    """A distribution's normalized name (PEP 503): ``My_Project.5G`` → ``my-project-5g``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _refuse(reference: str, why: str) -> NoReturn:
    raise ConfigurationError(f"{reference}: a pkg:// reference {why} — write pkg://<distribution>/<path>")


def _split(reference: str) -> Tuple[str, PurePosixPath]:
    """The distribution and the path inside its folder; ``..`` is refused (the path stays inside the project)."""
    name, _, rest = reference[len(PACKAGE_SCHEME) :].partition("/")
    if not name:
        _refuse(reference, "names no distribution")
    parts = [part for part in rest.split("/") if part not in ("", ".")]
    if not parts:
        _refuse(reference, f"names no file of {name}")
    if ".." in parts:
        _refuse(reference, "uses '..'")
    return name, PurePosixPath(*parts)


def _editable_checkout(dist: metadata.Distribution) -> Optional[Path]:
    """The checkout an editable install points at (PEP 610 ``direct_url.json``), or None for any other install."""
    text = dist.read_text("direct_url.json")
    if not text:
        return None
    try:
        info = json.loads(text)
    except ValueError:
        return None
    dir_info = info.get("dir_info") if isinstance(info, dict) else None
    if not isinstance(dir_info, dict) or not dir_info.get("editable"):
        return None
    url = urlparse(str(info.get("url", "")))
    if url.scheme != "file":
        return None
    return Path(url2pathname(url.path)).resolve()


def resolve_package_reference(reference: str) -> Path:
    """The file ``pkg://<distribution>/<path>`` names: in the editable checkout, else where the wheel installed it.

    Raises:
        ConfigurationError: the reference is malformed (no distribution, no path, a ``..``).
        ConfigFileNotFoundError: the distribution is not installed, or does not have the file.
    """
    name, rel = _split(reference)
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        raise ConfigFileNotFoundError(f"{reference}: the distribution {name!r} is not installed") from None
    installed_name = dist.metadata["Name"] or name
    label = f"{installed_name} {dist.version}"
    checkout = _editable_checkout(dist)
    if checkout is not None:
        path = checkout.joinpath(*rel.parts)
        if path.is_file():
            return path
        raise ConfigFileNotFoundError(f"{reference}: {label} (editable, {checkout}) has no {rel}")
    wanted = (SHARE_DIR, canonical_name(installed_name), *rel.parts)
    for record in dist.files or ():
        if PurePosixPath(str(record)).parts[-len(wanted) :] == wanted:
            path = Path(str(dist.locate_file(record))).resolve()
            if path.is_file():
                return path
    raise ConfigFileNotFoundError(f"{reference}: {label} ships no {rel}")
