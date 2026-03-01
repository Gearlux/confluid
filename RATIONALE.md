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
| **Confluid** | Post-Construction + DI | **Optimized** | Decouples creation from config; supports full hierarchy dumping/reconstruction. | New implementation. |

---

## Why Confluid? (The Value Proposition)

Confluid evolves the concepts of "post-construction configuration" into a modern, type-safe framework.

### 1. Post-Construction Configuration
In ML, models or datasets are often instantiated with defaults or partially loaded before full settings are known. Confluid allows configuring **existing** instances without requiring them to be rebuilt, while ensuring new instances remain unaffected.

### 2. Strict Gated Hierarchy
Unlike general-purpose serializers that attempt to dump every attribute, Confluid implements a **Strict Gate**. It will only recurse into sub-objects if they are explicitly marked as `@configurable`. This prevents "serialization sprawl" into deep third-party library internals (like PyTorch or TensorFlow tensors/buffers).

### 3. Third-Party Integration
Confluid provides a registration mechanism for objects from third-party libraries (e.g., `torch.optim.Adam`, `sklearn.svm.SVC`) that cannot be directly decorated. Once registered, these objects are treated as first-class configurable nodes within the Confluid hierarchy.

### 4. Smart Reference Resolution (`@` Syntax)
Confluid bridges the gap between YAML and Source Code.
- **Dependency Graph:** Define your model hierarchy in YAML using `@ClassName(...)`.
- **DRY Configuration:** Use `@key` to reference other values in the same file, ensuring a single source of truth for paths and hyperparameters.

### 5. Round-Trip Reproducibility
Confluid is designed for the **Dump -> Reconstruct** lifecycle.
- **Dumping:** Export the exact runtime state of a complex trainer (including its model, optimizer, and datasets) to a clean, human-readable YAML file.
- **Reconstruction:** Use that exported file to recreate the *entire* object graph in a new process, guaranteeing identical results.

### 6. Type-Safe Validation (Pydantic Core)
By leveraging Pydantic v2 internally, Confluid provides strict type coercion and validation at the moment of configuration. It catches "wrong-type" errors (e.g., passing a string where a float is expected) before the training loop begins.

---

## Design Goals
- **Explicit over Implicit:** If it's not marked `@configurable` or explicitly registered, it's not a config node.
- **Reproducibility First:** The final config dump MUST be able to reconstruct the object graph.
- **IDE Friendly:** Automatic JSON schema generation for YAML auto-complete.
- **Zero Blocking:** Lightweight, non-blocking configuration application.
