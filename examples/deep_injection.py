"""Deep injection — a real-world broadcasting scenario (think gin-config).

A document-ingest service is a four-level component tree:

    Pipeline -> Stage (fetch/transform/publish) -> Worker -> RetryPolicy

Nobody wants to thread ``verbose`` or ``timeout_s`` through four constructors
just so the leaf can see them. With confluid you don't: a bare top-level YAML
key broadcasts to every component that accepts it — ZERO plumbing code — while
addressed keys stay surgical and globs scope a subtree.

This is the *scenario* companion to ``examples/broadcasting.py`` (the
mechanics reference and docs-twin of ``docs/broadcasting.md``): same rules,
demonstrated at the scale where they pay off.
"""

from typing import List, Optional

from confluid import NoBroadcast, configurable, load


@configurable
class RetryPolicy:
    def __init__(self, max_attempts: int = 3, backoff_s: float = 1.0, timeout_s: float = 10.0) -> None:
        """Retry behaviour for a single worker — the DEEPEST leaf of the tree.

        Args:
            max_attempts: Attempts before giving up.
            backoff_s: Sleep between attempts.
            timeout_s: Per-attempt timeout.
        """
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.timeout_s = timeout_s


@configurable
class Worker:
    def __init__(
        self,
        name: NoBroadcast[str] = "worker",
        concurrency: int = 1,
        timeout_s: float = 10.0,
        retry: Optional[RetryPolicy] = None,
        verbose: bool = False,
    ) -> None:
        """The unit that actually processes documents.

        Args:
            name: Identity label — too generic to accept a broadcast ``name:``.
            concurrency: Parallel documents per worker.
            timeout_s: Per-document timeout.
            retry: The worker's retry policy.
            verbose: Chatty processing.
        """
        self.name = name
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.retry = retry
        self.verbose = verbose


@configurable
class Stage:
    def __init__(
        self,
        name: NoBroadcast[str] = "stage",
        worker: Optional[Worker] = None,
        timeout_s: float = 30.0,
        verbose: bool = False,
    ) -> None:
        """One step of the pipeline (fetch, transform, publish).

        Args:
            name: Identity label — opts out of bare-key broadcasting.
            worker: The stage's worker.
            timeout_s: Whole-stage timeout.
            verbose: Chatty execution.
        """
        self.name = name
        self.worker = worker
        self.timeout_s = timeout_s
        self.verbose = verbose


@configurable
class Pipeline:
    def __init__(
        self,
        stages: Optional[List[Stage]] = None,
        max_inflight: int = 2,
        timeout_s: float = 60.0,
        verbose: bool = False,
    ) -> None:
        """The service root.

        Args:
            stages: Ordered pipeline stages.
            max_inflight: Documents in flight across the pipeline.
            timeout_s: End-to-end timeout.
            verbose: Chatty orchestration.
        """
        self.stages = stages or []
        self.max_inflight = max_inflight
        self.timeout_s = timeout_s
        self.verbose = verbose


# The service topology: three stages, each with a worker, each worker with a
# retry policy. Written once — every demo below layers overrides on top of it.
TREE = """
pipeline: !class:Pipeline()
  stages:
    - !class:Stage()
      name: fetch
      worker: !class:Worker()
        name: fetch-worker
        retry: !class:RetryPolicy()
    - !class:Stage()
      name: transform
      worker: !class:Worker()
        name: transform-worker
        retry: !class:RetryPolicy()
    - !class:Stage()
      name: publish
      worker: !class:Worker()
        name: publish-worker
        retry: !class:RetryPolicy()
"""


def build(overrides: str = "") -> Pipeline:
    pipeline = load(TREE + overrides)["pipeline"]
    assert isinstance(pipeline, Pipeline)
    return pipeline


def worker_of(stage: Stage) -> Worker:
    assert stage.worker is not None
    return stage.worker


def retry_of(stage: Stage) -> RetryPolicy:
    retry = worker_of(stage).retry
    assert retry is not None
    return retry


def main() -> None:
    # ---- 1. The gin pitch: configure deep internals with ZERO plumbing -----
    # Two bare keys reach depth 1 (pipeline), 2 (stages), 3 (workers), and 4
    # (retry policies) — no constructor threading, no context object, no glue.
    p = build("verbose: true\ntimeout_s: 5.0\n")
    assert p.verbose and p.timeout_s == 5.0
    for stage in p.stages:
        assert stage.verbose and stage.timeout_s == 5.0
        assert worker_of(stage).verbose and worker_of(stage).timeout_s == 5.0
        assert retry_of(stage).timeout_s == 5.0, "the bare key reached depth 4"
    print("bare keys:      verbose/timeout_s landed on all 10 components (depth 1..4), zero plumbing")

    # ---- 2. Addressed keys are EXACT: surgical, no cascade -----------------
    p = build("pipeline.max_inflight: 8\npipeline.timeout_s: 99.0\n")
    assert p.max_inflight == 8 and p.timeout_s == 99.0
    assert p.stages[0].timeout_s == 30.0, "addressed 'pipeline.timeout_s' did NOT cascade to descendants"
    print("addressed keys: pipeline.timeout_s=99.0 hit the pipeline ONLY (stages kept 30.0)")

    # ---- 3. A class-name block targets every instance of one class ---------
    p = build("Worker:\n  concurrency: 4\n")
    assert all(worker_of(s).concurrency == 4 for s in p.stages)
    assert p.max_inflight == 2, "other classes untouched"
    print("class block:    Worker.concurrency=4 on all three workers, nothing else")

    # ---- 4. Globs opt a SUBTREE back into the cascade ----------------------
    # '**' = zero or more levels below pipeline; '*' would mean exactly one.
    p = build('"pipeline.**.backoff_s": 0.1\n')
    assert all(retry_of(s).backoff_s == 0.1 for s in p.stages)
    print("glob key:       pipeline.**.backoff_s=0.1 reached every RetryPolicy in the subtree")

    # ---- 5. Generic names are protected: NoBroadcast[str] ------------------
    # A stray bare 'name:' would otherwise clobber every stage AND worker.
    p = build("name: oops\n")
    assert [s.name for s in p.stages] == ["fetch", "transform", "publish"]
    assert worker_of(p.stages[0]).name == "fetch-worker"
    print("NoBroadcast:    the bare 'name: oops' key touched none of the six name labels")

    # ---- 6. One priority rule: document order, last write wins -------------
    # There are no specificity tiers: the Worker block applies first, then the
    # LATER bare key overwrites it — position in the document is the only rule.
    p = build("Worker:\n  timeout_s: 5.0\ntimeout_s: 2.0\n")
    assert all(worker_of(s).timeout_s == 2.0 for s in p.stages)
    print("last write:     Worker block set timeout_s=5.0, the later bare key won with 2.0")


if __name__ == "__main__":
    main()
