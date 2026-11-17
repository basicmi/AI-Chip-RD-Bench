# Phase 31 — Architecture Search Space Contract

## Goal

Move from free-form RTL patching toward an explicit, machine-readable description
of architecture choices. The first implementation is intentionally an inventory
and provenance contract, not an RTL generator.

## CORDIC v1 search space

The space is bound to `cordic.rotation.q14.v1` and keeps the task invariants
fixed: module interface, synchronous active-high reset, maximum 32-cycle
completion, and 32-LSB numerical tolerance.

| Dimension | Choice | Bound RTL |
|---|---|---|
| datapath_update_form | `conditional_add_sub` | Golden iterative CORDIC |
| datapath_update_form | `signed_delta_shared_add` | Existing behavior-preserving signed-delta refactor |

The two candidates share the same state, iteration schedule and interface.
This first space therefore isolates one implementation-form dimension; it
does not yet claim to explore pipeline depth, iteration parallelism, word width,
or latency/area tradeoffs.

## Validation

Run:

```bash
python -m chiprd.cli architecture-space-validate
```

The validator checks:

- version, task and top-module identity;
- unique dimension and candidate IDs;
- each candidate specifies every declared dimension exactly once;
- every declared enum value has at least one concrete RTL binding;
- no duplicate configurations or duplicate RTL paths;
- repository-contained, non-symlink RTL paths;
- actual top-module declaration in each bound RTL file;
- SHA-256 fingerprints for every RTL binding;
- a declared baseline candidate exists.

CI runs this validation alongside task-registry validation. Unit tests cover
invalid values, incomplete configuration, duplicate configurations, path
escape, wrong top module and missing enum coverage.

## Evidence boundary

A valid architecture-space file only proves that the inventory is internally
consistent and that each declared choice points to a concrete RTL source. It
does not prove functional equivalence, protocol correctness, PPA improvement,
orthogonality of dimensions, or completeness of the search space.

Every candidate must still pass the task's independent functional, formal,
equivalence, mutation and synthesis gates. PPA ranking is allowed only after
the mandatory gates and timing constraints pass. The current CORDIC PPA
evidence remains Nangate45 Liberty mapping plus ideal-clock pre-layout STA,
not routed signoff or power.

## Next extension

The next step is to add controlled architecture generators for meaningful
microarchitectural dimensions, with explicit cost and legality constraints.
Good candidates include iteration parallelism (1/2/4 stages per cycle),
datapath sharing versus duplicated add/sub resources, and LUT/constant
implementation choices. Each generator must emit a source hash and preserve
the task invariants before it can enter the existing sealed evaluation flow.
