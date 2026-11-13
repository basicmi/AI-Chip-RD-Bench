# AI-Chip-RD-Bench

A reproducible, evidence-gated benchmark for **AI autonomous chip R&D**.

## Phase 0: CORDIC Golden Task

This repository provides a fixed-point CORDIC task, reference RTL, analytic mathematical oracle, Verilator testbench, Yosys generic synthesis adapter, simulation mutation campaign, protocol-level formal checks, cycle-accurate sequential equivalence checks, sealed experiment manifests, a JSONL experiment ledger and reproducibility checks.

**Scope:** functional simulation, mutation sensitivity, protocol-level formal proof, cycle-accurate sequential equivalence and generic synthesis only. Generic cell count is not physical PPA. Formal numerical equivalence, latency-insensitive equivalence, private held-out tests, actual PDK/SDC/OpenROAD flows and autonomous RTL generation remain future milestones. A budgeted proposal scheduler is provided, but it evaluates externally generated candidates rather than generating RTL.

### Quickstart (Ubuntu 22.04/24.04)

Install Verilator, Yosys, Z3, SymbiYosys and a compatible EQY toolchain. For a reproducible EQY/Yosys/SBY/Z3 environment, use the pinned OSS CAD Suite release recorded in CI.

Run the individual checks:

```bash
python3 -m unittest discover -s tests -v
python3 -m chiprd.cli all --candidate tasks/cordic/cordic_rotator.sv --output artifacts/ci
python3 -m chiprd.cli mutation --candidate tasks/cordic/cordic_rotator.sv --output artifacts/mutation
python3 -m chiprd.cli formal --candidate tasks/cordic/cordic_rotator.sv --output artifacts/formal
python3 -m chiprd.cli formal-negative-control --candidate tasks/cordic/cordic_rotator.sv --output artifacts/formal-negative-control
python3 -m chiprd.cli equivalence-selftest --output artifacts/equivalence
```

### Sealed end-to-end experiment

The experiment command seals the candidate RTL once, then runs all public checks against that immutable snapshot:

```bash
python3 -m chiprd.cli experiment \
  --candidate tasks/cordic/cordic_rotator.sv \
  --output artifacts/experiment
```

It produces:

- `sealed/candidate.sv`: read-only candidate snapshot.
- `experiment_manifest.json`: candidate SHA-256, evaluator-input hashes, tool versions, Python/platform, seed, formal depth, equivalence semantics and command plan.
- `experiment_ledger.jsonl`: append-only, hash-chained lifecycle events for sealing, check start/completion and finalization. Each event commits to the previous event; completed checks record the report path and SHA-256.
- `experiment_report.json`: aggregate status, per-check report pointers and normalized result signature.
- Per-check folders containing the original detailed logs, reports, partitions and counterexample traces.

The unified experiment runs functional verification plus generic synthesis, simulation mutation, protocol formal proof, formal negative control, candidate-to-reference EQY, and the EQY positive/negative self-test. All candidate-specific checks use the same sealed candidate hash. The EQY harness self-test is a separate, fixed positive/negative fixture that validates the equivalence toolchain's ability to prove a behavior-preserving refactor and reject a deliberate mismatch. A missing tool, an inconclusive proof or a missing report fails closed; it is never silently counted as a pass.

### Reproduce an experiment

Use the original manifest as the reproduction contract:

```bash
python3 -m chiprd.cli reproduce \
  --manifest artifacts/experiment/experiment_manifest.json \
  --output artifacts/reproduced
```

Before rerunning, the reproducer verifies:
1. The sealed candidate exists and its SHA-256 matches.
2. Every evaluator input (RTL reference, testbench, oracle, formal harness, runners, task specification and package metadata) matches the original manifest.
3. The required tool versions, Python version and platform match the original toolchain record.
4. The hash-chained ledger is intact, its first event binds the original manifest hash, and its final event binds the experiment report and normalized result signature.

If any precondition differs, reproduction is blocked and the mismatch is recorded; it does not quietly substitute the current evaluator. The ledger detects accidental edits, deletion, reordering or truncation of recorded events; it is tamper-evident, not a cryptographic signature from a trusted external authority. If preconditions match, the full experiment is rerun and normalized result signatures are compared. Timestamps, temporary directories and log paths are deliberately excluded from the result signature; functional outcomes, synthesis statistics, mutation outcomes and formal/equivalence proof statuses are included.

A reproduction PASS means the normalized experiment results match under the same recorded evaluator inputs and tool versions. It does not mean the experiment is a secret test, an adversarial sandbox, a physical PPA sign-off or a silicon proof.

### Simulation mutation campaign

Five public RTL mutations are injected. The campaign passes only when the golden candidate passes and all five mutants are rejected by the independent functional evaluator. Tool/evaluator errors are not counted as kills.

### Formal protocol proof

The SymbiYosys task checks:
- Completion valid is exactly one cycle.
- No completion occurs without a previously accepted request.
- Every accepted request completes within the task's **32-cycle maximum latency**; the task does not require a specific implementation latency.
- Output registers remain stable when no completion is valid.

The harness assumes synchronous reset on the first formal clock and deasserted reset thereafter, preventing reset from masking an outstanding-request timeout. The runner uses `smtbmc z3` in SBY `prove` mode. It reports `proof_status: proven` only for SBY's explicit PASS marker. Unknown results and tool errors are not passes. A separate negative-control run suppresses the completion-valid pulse and requires the formal assertions to fail.

**Formal scope is limited:** this does not prove sine/cosine numerical accuracy, complete functional equivalence, CDC, reset recovery/removal, physical timing, area, power or silicon correctness. Numerical accuracy remains covered by the independent Verilator + analytic oracle.

### Sequential equivalence (EQY)

EQY compares a known-good **gold** RTL against a candidate **gate** RTL using Yosys partitioning and an SBY proof strategy. This repository's initial equivalence contract is deliberately **cycle-accurate**: top-level outputs must match on every clock for the same input/reset sequence. It is suitable for behavior-preserving RTL rewrites and many internal implementation changes when the external latency and protocol remain unchanged.

The self-test includes:
- A structural CORDIC refactor that factors conditional add/subtract operations into signed delta terms while preserving state, interface and latency. It must prove equivalent.
- A deliberately incorrect rotation-delta mutation. EQY must reject it.

**Important boundary:** this is cycle-by-cycle sequential equivalence, not latency-insensitive equivalence. A pipeline retiming that changes externally visible response cycles may be functionally valid under a transaction-level specification but will not satisfy this strict contract. Such transformations need a separate latency/refinement checker rather than weakening the cycle-accurate result. EQY also does not replace the analytic numerical oracle or protocol-property proof.

### Agent proposal API and budgeted search

The search runner accepts a versioned JSON proposal pool. An external Agent or user creates candidate RTL files inside the repository workspace and submits their repository-relative paths, lineage and optional predicted improvement:

\`\`\`json
{
  "schema_version": "1.0.0",
  "task_id": "cordic.rotation.q14.v1",
  "proposals": [
    {
      "candidate_id": "baseline",
      "candidate_path": "tasks/cordic/cordic_rotator.sv",
      "generation": 0,
      "predicted_improvement": 0.0
    },
    {
      "candidate_id": "signed_delta_refactor",
      "candidate_path": "tasks/cordic/cordic_rotator_refactor.sv",
      "parent_id": "baseline",
      "generation": 1,
      "predicted_improvement": 0.0
    }
  ]
}
\`\`\`

Run the evolutionary baseline with a fixed budget:

\`\`\`bash
python3 -m chiprd.cli search \\
  --proposals examples/search/cordic_candidate_pool.json \\
  --strategy evolutionary \\
  --max-experiments 2 \\
  --max-wall-clock-seconds 1200 \\
  --max-experiment-seconds 600 \\
  --max-candidate-bytes 1048576 \\
  --output artifacts/search \\
  --seed 20261113
\`\`\`

Three reproducible scheduling baselines are available:
- **Random:** seeded random order within each generation.
- **Greedy:** descending Agent-supplied \`predicted_improvement\` within each generation. This is a proposal-priority heuristic, not a measured reward.
- **Evolutionary:** generation-based lineage scheduling. Only children of the top feasible parent fraction from the immediately preceding generation are eligible. Parent fitness is measured by the evaluator.

The budget limits total experiment count, campaign wall-clock time, per-experiment runtime and candidate file size. Each experiment runs in a separate process group; on timeout, the runner terminates the process group before recording a timeout result. Candidate paths must resolve to regular files inside the repository, parent links must be valid and point to the immediately preceding generation, and candidate bytes are re-hashed before execution.

A candidate is feasible only if its sealed source hash matches the submitted proposal, the snapshot remains intact, all six public gates pass, and a valid generic synthesis cell count is present. The current objective minimizes Yosys generic cell count subject to those hard correctness gates. This is a coarse synthesis proxy, not physical area, timing, power or PPA.

The campaign writes:
- \`search_manifest.json\`: proposal-pool hash, scheduler/CLI hashes, strategy, seed, budget and objective contract.
- \`search_report.json\`: budget consumption, candidate lineage, gate outcomes, feasibility, fitness, baseline comparison, best candidate and skipped-proposal reasons.
- \`experiments/<candidate_id>/\`: each candidate's sealed manifest, hash-chained ledger, full evaluator reports and runner logs.

**Important API boundary:** this PR implements the candidate submission schema, validation, scheduling, budget enforcement and evidence aggregation. It does not implement an LLM Agent, RTL patch generation, mutation/recombination operators or a closed-loop proposer. Those can be connected through the proposal-pool interface in a later phase.

### Evidence boundaries

- Generic cell count is not standard-cell area, timing or power.
- Public mutation tests measure sensitivity to the listed mutations, not broad fault coverage.
- The repository contains public tests, not secret hidden tests.
- The harness is not an adversarial sandbox; execute untrusted HDL only in isolated runners.
- No production PPA, complete formal functional proof, silicon measurement or tape-out assertion is made.

See [CORDIC specification](tasks/cordic/spec.md).
