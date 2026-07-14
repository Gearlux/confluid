# I/O Contract: `@output` Properties & `Mandatory[T]` Inputs

A *Runnable* class (a trainer/evaluator — anything whose product is consumed
downstream) declares an explicit **I/O contract** that GUIs and agents read from
one source: which `@property` getters are its **outputs**, and which inputs are
**mandatory** vs **nullable**. Visual editors (output sockets + required vs optional
input sockets), form-spec builders, and MCP schemas can all consume it.

```python
import torch.nn as nn
from typing import Any, Optional, Union
from confluid import Mandatory, configurable, output, input_specs, output_specs

@configurable
class Trainer:
    def __init__(
        self,
        model: Mandatory[Union[nn.Module, Any]],   # mandatory input (must be wired)
        num_classes: Optional[int] = None,          # nullable / optional
    ) -> None:
        self.model = model
        self.num_classes = num_classes

    @property
    @output                                         # NOTE: @output UNDER @property
    def trained_model(self) -> nn.Module:
        """The trained model produced by run()."""
        return self.model

output_specs(Trainer)   # [{'name': 'trained_model', 'type': 'Module', 'description': '...'}]
input_specs(Trainer)    # [{'name': 'model', 'required': True, 'nullable': False, ...},
                        #  {'name': 'num_classes', 'required': False, 'nullable': True, ...}]
```

* **`@output`** (mirrors `@ignore_config`) marks a read-only `@property` getter as
  a declared output. Apply it **under** `@property` so it stamps the getter, not
  the `property` object. Because the property is read-only/derived, it is already
  excluded from `to_pydantic` — it never becomes a config knob and round-trips
  cleanly. `output_specs(cls)` enumerates them (MRO-walked; subclass override wins).
* **`Mandatory[T]`** (an `Annotated` marker, mirroring `Lazy[T]`; named to avoid
  `typing.Required` confusion) flags an input mandatory **even when it carries a
  default** for zero-arg construction — the structural signal (no default /
  non-`Optional`) already implies mandatory, but the marker restores the contract
  when the **Zero-Arg Construction** mandate (see [Class Design](class-design.md))
  forces a default onto a genuinely required class/`Fluid` slot. `input_specs(cls)`
  reports `{required, nullable}` per param (`required = no-default OR Mandatory`).
  The marker is stripped by `to_pydantic`, so it never leaks into the JSON Schema,
  and composes with `Lazy` (`Mandatory[Lazy[T]]`).

## Runnable example

[`examples/io_contract.py`](../examples/io_contract.py) declares a Runnable with
an `@output` property and a defaulted-but-`Mandatory` input, then prints what
`output_specs` / `input_specs` report.
