"""Threads & async — companion to ``docs/concurrency.md``.

The engine state rides a ``contextvars.ContextVar``: ``active_context`` makes a
bare ``flow()`` resolve references, a raw ``threading.Thread`` starts with a
clean context, and ``contextvars.copy_context()`` carries it over.
"""

import contextvars
import threading
from typing import Any, List

from confluid import Reference, ReferenceResolutionError, active_context, configurable, flow


@configurable
class Model:
    def __init__(self, layers: int = 3) -> None:
        """A model exposing parameters for the classic optimizer wiring.

        Args:
            layers: Layer count.
        """
        self.layers = layers

    def parameters(self) -> List[str]:
        """Stand-in for torch's ``Module.parameters()``."""
        return [f"w{i}" for i in range(self.layers)]


def main() -> None:
    model = Model(layers=2)

    # active_context makes a bare flow() resolve dotted references (incl. a method call).
    with active_context({"model": model}):
        params = flow(Reference("model.parameters()"))
        assert params == ["w0", "w1"]
        print(f"main thread, inside active_context: {params}")

        # A raw Thread starts with a CLEAN context — the reference cannot resolve there.
        def resolve_in_worker(sink: List[Any]) -> None:
            try:
                sink.append(flow(Reference("model.parameters()")))
            except ReferenceResolutionError as exc:
                sink.append(exc)

        naked_result: List[Any] = []
        t = threading.Thread(target=resolve_in_worker, args=(naked_result,))
        t.start()
        t.join()
        assert isinstance(naked_result[0], ReferenceResolutionError), "raw thread saw no context"
        print(f"raw Thread (no context):   raised {type(naked_result[0]).__name__}")

        # copy_context() carries the caller's context into the worker.
        ctx = contextvars.copy_context()
        carried_result: List[Any] = []
        t2 = threading.Thread(
            target=lambda: ctx.run(lambda: carried_result.append(flow(Reference("model.parameters()"))))
        )
        t2.start()
        t2.join()
        assert carried_result[0] == ["w0", "w1"]
        print(f"Thread via copy_context(): {carried_result[0]}")


if __name__ == "__main__":
    main()
