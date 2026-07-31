# Using Confluid Across Threads & Async

The engine's materialization state (the active resolution context, the
shared-instance memos, the `solidify` suppression flag) rides a
`contextvars.ContextVar` — so it follows Python's standard context
propagation rules:

- **Inherited automatically**: asyncio tasks (`asyncio.create_task`) and
  `asyncio.to_thread` workers see the caller's active context.
- **NOT inherited**: a raw `threading.Thread` or `loop.run_in_executor`
  worker starts with a clean context.

To make a bare `flow()` resolve `!ref:`/broadcasts outside a
`materialize()` pass — including on another thread — activate a context
explicitly with the public `active_context`:

```python
from confluid import Reference, active_context, flow

with active_context({"model": model}):
    optimizer = flow(Reference("model.parameters()"))
```

For a raw thread or executor, either enter `active_context(...)` inside the
worker function, or capture and run the caller's context:

```python
import contextvars, threading

ctx = contextvars.copy_context()
threading.Thread(target=lambda: ctx.run(work)).start()   # inherits the context

# asyncio: prefer asyncio.to_thread(work) over loop.run_in_executor —
# to_thread propagates contextvars, the raw executor does not.
```

`active_context` is nesting-safe (the previous state is restored on exit)
and installs fresh instance-sharing memos, so dotted refs inside the block
share one materialized instance. The mapping is used verbatim when it has
no dotted keys (live objects keep their identity); dotted keys are expanded
like `materialize` does.

## Runnable example

[`examples/concurrency.py`](../examples/concurrency.py) resolves a dotted
`Reference` under `active_context`, shows a raw `threading.Thread` starting
clean, and fixes it with `contextvars.copy_context()`.
