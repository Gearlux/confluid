"""The module every generated schema mirror lives in.

:func:`confluid.pydantic_export.to_pydantic` creates each model with ``__module__ = "confluid._models"`` and, once a
model is built, registers the forward reference a cycle names it by (``_ConfluidModel_<id>``) as a name here. A
forward reference resolves in its model's own module, so pydantic's public ``model_rebuild()`` finds it with no
private argument — the one reason this module exists. Nothing is defined here by hand: ``pydantic_export`` writes the
names and ``to_pydantic.cache_clear()`` removes them (``docs/architecture.md`` record 29).
"""
