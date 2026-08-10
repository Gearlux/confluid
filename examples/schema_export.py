"""Schema export — companion to ``docs/schema-export.md``.

One decorated class drives the whole surface: ``to_pydantic`` generates a
validating pydantic model from the constructor signature (docstring ``Args:``
become field descriptions, ``Annotated`` constraints carry into the JSON
schema), ``parse_param_docs`` reads the docstring on its own, ``validate_model``
checks a config mapping, ``confluid_class_of`` names the origin class, and
``sanitize_schema`` downgrades the JSON Schema to the subset strict LLM
function-calling APIs accept.

Requires the ``confluid[pydantic]`` extra (plain ``confluid`` degrades:
``to_pydantic`` raises ``ImportError`` naming it).
"""

import json
from typing import Annotated, Optional

from pydantic import ValidationError

from confluid import configurable, confluid_class_of, parse_param_docs, sanitize_schema, to_pydantic, validate_model

try:
    from annotated_types import Interval
except ImportError:  # pragma: no cover - annotated_types ships with pydantic
    raise SystemExit("this example needs confluid[pydantic]")


@configurable
class Trainer:
    """A trainer with documented, constrained knobs.

    Args:
        lr: Learning rate for the optimizer.
        epochs: How many passes over the training set.
        device: Compute device, or None to auto-select.
    """

    def __init__(
        self,
        lr: Annotated[float, Interval(gt=0.0, le=1.0)] = 0.001,
        epochs: int = 10,
        device: Optional[str] = None,
    ) -> None:
        self.lr = lr
        self.epochs = epochs
        self.device = device


def main() -> None:
    # 1. Signature -> pydantic model. Every constructor param becomes a field;
    #    the docstring Args: lines become field descriptions.
    model_cls = to_pydantic(Trainer)
    schema = model_cls.model_json_schema()
    assert schema["properties"]["lr"]["description"] == "Learning rate for the optimizer."
    assert schema["properties"]["lr"]["exclusiveMinimum"] == 0.0  # the Interval mark carried through
    print(f"fields: {sorted(schema['properties'])}")

    # 2. The model VALIDATES — a config mapping is checked before anything runs.
    ok = model_cls(lr=0.01, epochs=3)
    assert ok.model_dump()["lr"] == 0.01
    try:
        model_cls(lr=5.0)  # out of the declared (0, 1] range
    except ValidationError as exc:
        print(f"rejected: {exc.error_count()} error(s) for lr=5.0")
    else:
        raise AssertionError("an out-of-range lr must fail validation")

    # 3. validate_model re-validates an ALREADY-BUILT model under a policy mode
    #    ("strict" raises, "warn" logs without raising) — the hook a tool server
    #    uses to apply the same mode logic at its own entry point.
    validate_model(ok, "strict")  # a valid instance passes silently
    print("validate_model: strict re-validation passed")

    # 4. The generated model remembers its origin class (module-qualified) — a
    #    schema consumer can emit the right `!class:` target with no side table.
    origin = confluid_class_of(model_cls)
    assert origin is not None and origin.split(".")[-1] == "Trainer", origin
    print(f"origin class: {origin}")

    # 5. parse_param_docs reads the docstring on its own — the same source the
    #    field descriptions came from, for consumers that skip pydantic.
    docs = parse_param_docs(Trainer)
    assert docs["epochs"] == "How many passes over the training set."

    # 6. sanitize_schema: strict LLM function-calling APIs reject $ref/$defs,
    #    allOf, and rich anyOf. The sanitized schema inlines and collapses —
    #    `Optional[str]` becomes a plain nullable string — without touching
    #    server-side validation.
    raw = model_cls.model_json_schema()
    safe = sanitize_schema(raw)
    assert "$defs" not in json.dumps(safe)
    device = safe["properties"]["device"]
    assert "anyOf" not in device and device.get("nullable") is True
    print(f"sanitized device field: {device}")


if __name__ == "__main__":
    main()
