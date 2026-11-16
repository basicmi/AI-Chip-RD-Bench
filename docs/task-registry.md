# Phase 23 — Task Registry and Benchmark Track Catalog

The benchmark previously had one executable task, CORDIC, with its identity and
RTL paths embedded in the harness. This phase introduces a versioned catalog
that makes task coverage explicit before additional IP families are added.

## List tracks and task maturity

```bash
python3 -m chiprd.cli task-list
python3 -m chiprd.cli task-validate
```

Equivalent standalone module:

```bash
python3 -m chiprd.task_registry list
python3 -m chiprd.task_registry validate
```

The registry is `tasks/registry.json`. It distinguishes:

- **Tracks**: benchmark families and their maturity (`planned`, `active`,
  `complete`).
- **Tasks**: versioned, concrete evaluation targets with maturity
  (`planned`, `runnable`, `deprecated`).
- **Runnable task contract**: spec, Golden/reference RTL, top-level interface,
  reset and latency semantics, evaluator set, primary objective, hard
  constraints and known limitations.

The validator checks unique IDs, track references, safe repository-relative
artifact paths, file existence, interface/latency declarations and a complete
six-part evaluator declaration for every runnable task. It never executes RTL
or EDA tools.

## Current coverage

| Track | Maturity | Runnable tasks |
| --- | --- | ---: |
| Legacy IP reimplementation | Planned | 0 |
| IP rewrite, feature addition and optimization | Active | 1 (CORDIC) |
| IP porting and technology adaptation | Planned | 0 |

CORDIC is currently classified as an IP feature-evolution task because its
baseline supports behavior-preserving RTL rewrites and PPA-oriented
implementation optimization. It is **not** a representative legacy-IP
reimplementation or technology-porting benchmark.

## Contract for adding a new runnable task

A new task must not be labelled runnable until it has:

1. Versioned behavioral specification and explicit interface/reset/latency contract.
2. Golden RTL and independent reference/oracle where numerical behavior applies.
3. Functional tests, protocol properties, and an equivalence contract whose
   semantics are explicit (cycle-accurate or transaction/latency-aware).
4. Applicable mutation tests and negative controls.
5. Reproducible synthesis and, where claimed, technology-mapped PPA constraints.
6. Independent post-selection audit policy and reproducible evidence format.
7. A task-specific baseline and objective; metrics from unlike tasks must not
   be pooled into a single model ranking.

The registry is descriptive and fail-closed, but the current evaluator pipeline
still contains CORDIC-specific assumptions. **Registering a task does not make
the current Agent/PPA runner generic.** The next engineering step is to extract
task-specific evaluator adapters from the shared campaign/controller layer,
then implement a second end-to-end task to prove the abstraction works.

## Validation

```bash
python3 -m unittest discover -s tests -p 'test_task_registry.py' -v
python3 -m unittest discover -s tests -v
```
