# Confluid: Rationale & Architectural Design

## Executive Summary
**Confluid** is a hierarchical configuration and dependency injection framework designed for the complex object graphs typical in Machine Learning (ML) and High-Performance Computing (HPC). It serves as the "glue" in the research-to-production pipeline, ensuring that every experiment run is modular, validated, and 100% reproducible.

---

## The Landscape: Existing Alternatives

| Library | Mechanism | ML Suitability | Pros | Cons |
| :--- | :--- | :--- | :--- | :--- |
| **Hydra** | Compositional YAML | **High** | Industry standard, massive plugin ecosystem. | Complex syntax; configuration *drives* instantiation (hard to wrap existing objects). |
| **Gin-Config** | Dependency Injection | **High** | Simple, powerful DI for deep learning. | Code-heavy; hard to export/dump final state back to YAML. |
| **Pydantic** | Schema Validation | **High** | Strict typing, excellent IDE support. | Not a hierarchical "config system" out-of-box; lacks @reference resolution. |
| **Confluid** | Post-Construction + DI | **Optimized** | Decouples creation from config; supports full hierarchy dumping/reconstruction; an unaddressed key reaches every node that accepts it, so a sweep sets one knob tree-wide with no parameter threading. | **Order-dependent** — position is the whole arbitration, so moving a line can change the result (see below); new implementation. |

---

## Why Confluid? (The Value Proposition)

Confluid evolves the concepts of "post-construction configuration" into a modern, type-safe framework.

### 1. Post-Construction Configuration
In ML, models or datasets are often instantiated with defaults or partially loaded before full settings are known. Confluid allows configuring **existing** instances without requiring them to be rebuilt, while ensuring new instances remain unaffected.

### 2. Strict Gated Hierarchy
Unlike general-purpose serializers that attempt to dump every attribute, Confluid implements a **Strict Gate**. It will only recurse into sub-objects if they are explicitly marked as `@configurable`. This prevents "serialization sprawl" into deep third-party library internals (like PyTorch or TensorFlow tensors/buffers).

### 3. Third-Party Integration
Confluid provides a registration mechanism for objects from third-party libraries (e.g., `torch.optim.Adam`, `sklearn.svm.SVC`) that cannot be directly decorated. Once registered, these objects are treated as first-class configurable nodes within the Confluid hierarchy.

### 4. Smart Reference Resolution (Plain YAML)
Confluid bridges the gap between YAML and Source Code, without leaving YAML behind:
markers are ordinary mappings carrying a reserved key, so `yaml.safe_load`, `yq`,
editor schemas and linters all still read the file.
- **Dependency Graph:** Define your model hierarchy with `{_target_: ClassName, ...}`.
- **DRY Configuration:** Use `${ref:key}` for a shared instance and `${a.b}` interpolation for a shared value, so a path or hyperparameter has one source of truth.

### 5. Round-Trip Reproducibility
Confluid is designed for the **Dump -> Reconstruct** lifecycle.
- **Dumping:** Export the exact runtime state of a complex trainer (including its model, optimizer, and datasets) to a clean, human-readable YAML file.
- **Reconstruction:** Use that exported file to recreate the *entire* object graph in a new process, guaranteeing identical results.

### 6. Robust Recursive Traversal
Confluid uses a recursive traversal engine that walks through object graphs (including lists and dictionaries) to identify and configure all nodes. Matching is **flat-view ordered last-write-wins**: when a class materializes, its visible context is the document with the descent path popped (each ancestor's wrapper key is replaced in place by its kwargs); scalar values whose key is in the receiving class's accept-list are applied in YAML document order, with later positions overriding earlier. Class-name (`ClassName: {...}`) and instance-name blocks are spliced inline at their document position. There is no static priority over explicit kwargs — every source takes its slot at its YAML position.

---

## The bet, and what it costs

Confluid's distinguishing choice is **implicit reach-many**: a key you did not
address reaches every node whose accept-list carries it, and precedence is
**document position, last spec wins**. No mainstream config library makes that
bet — Hydra and OmegaConf have no reach-many at all, gin-config requires you to
write `Class.param` at every site, and Fiddle makes it an explicit
`select(cfg, Trainer).set(...)` call.

The win is the one `examples/deep_injection.py` demonstrates: one bare key
configures a leaf four levels down, with zero parameter-threading code.

The cost is **order-dependence**. Because position is the arbitration, moving a
line — not just adding or removing one — can change the result. Libraries that
make reach-many explicit pay the opposite price in verbosity and get
order-independence back. The mitigations confluid ships for its side of the
trade are the accept-list and the `NoBroadcast` / `broadcast=False` opt-outs
(which bound *where* a key can land), and `ConfigurationReport.explain(key)`,
which prints the contest for a key in document order with the winner marked, so
a surprising value is one call rather than a bisect.

## Design Goals
- **Explicit over Implicit:** If it's not marked `@configurable` or explicitly registered, it's not a config node.
- **Reproducibility First:** The final config dump MUST be able to reconstruct the object graph.
- **Dotted-Path Resolution:** Support for complex dotted-path resolution (e.g. `model.layers: 10`).
- **Declarative Scopes:** Conditional overlays carry a `_scope_` / `_notscope_` entry whose value is a mapping of dimension to required value (ANDed), resolved in confluid. Boolean (`_scope_: {debug: }`) and keyed (`_scope_: {task: classification}`) forms share one grammar; a CLI layer forwards activations via the `scopes=` kwarg on `load()`.
- **Zero Blocking:** Lightweight, non-blocking configuration application.
