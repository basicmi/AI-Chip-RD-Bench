# AI-Chip-RD-Bench

A reproducible, evidence-gated benchmark for **AI autonomous chip R&D**.

## Phase 0: CORDIC Golden Task

This repository provides a fixed-point CORDIC task, reference RTL, analytic mathematical oracle, Verilator testbench, Yosys generic synthesis adapter, simulation mutation campaign, protocol-level formal checks, cycle-accurate sequential equivalence checks, sealed experiment manifests, a JSONL experiment ledger and reproducibility checks.

**Scope:** functional simulation, mutation sensitivity, protocol-level formal proof, cycle-accurate sequential equivalence and generic synthesis only. Generic cell count is not physical PPA. Formal numerical equivalence, latency-insensitive equivalence, private held-out tests and actual PDK/SDC/OpenROAD flows remain future milestones. The benchmark accepts externally generated, single-file unified diffs against the fixed CORDIC RTL, applies them to an isolated copy and records patch provenance. Its bounded closed-loop controller can invoke an external Agent adapter and feed experiment results back, but it does not bundle an LLM or claim autonomous model-driven optimization by itself. A budgeted proposal scheduler evaluates supplied candidates.

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

Five public source-pattern RTL mutations are defined. The candidate baseline must pass; every applicable mutant must be rejected by the independent functional evaluator. A mutation whose exact source pattern is absent is reported as `not_applicable`, excluded from the mutation-score denominator, and never counted as a kill. At least one mutation must be applicable, and any tool/evaluator error prevents a pass. Reports include both applicable coverage and mutation score so structurally different candidates are not silently credited with untested mutations.

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

```json
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
```

Run the evolutionary baseline with a fixed budget:

```bash
python3 -m chiprd.cli search \
  --proposals examples/search/cordic_candidate_pool.json \
  --strategy evolutionary \
  --max-experiments 2 \
  --max-wall-clock-seconds 1200 \
  --max-experiment-seconds 600 \
  --max-candidate-bytes 1048576 \
  --output artifacts/search \
  --seed 20261113
```

Three reproducible scheduling baselines are available:
- **Random:** seeded random order within each generation.
- **Greedy:** descending Agent-supplied `predicted_improvement` within each generation. This is a proposal-priority heuristic, not a measured reward.
- **Evolutionary:** generation-based lineage scheduling. Only children of the top feasible parent fraction from the immediately preceding generation are eligible. Parent fitness is measured by the evaluator.

The budget limits total experiment count, campaign wall-clock time, per-experiment runtime and candidate file size. Each experiment runs in a separate process group; on timeout, the runner terminates the process group before recording a timeout result. Candidate paths must resolve to regular files inside the repository, parent links must be valid and point to the immediately preceding generation, and candidate bytes are re-hashed before execution.

A candidate is feasible only if its sealed source hash matches the submitted proposal, the snapshot remains intact, all six public gates pass, and a valid generic synthesis cell count is present. The current objective minimizes Yosys generic cell count subject to those hard correctness gates. This is a coarse synthesis proxy, not physical area, timing, power or PPA.

The campaign writes:
- `search_manifest.json`: proposal-pool hash, scheduler/CLI hashes, strategy, seed, budget and objective contract.
- `search_report.json`: budget consumption, candidate lineage, gate outcomes, feasibility, fitness, baseline comparison, best candidate and skipped-proposal reasons.
- `experiments/<candidate_id>/`: each candidate's sealed manifest, hash-chained ledger, full evaluator reports and runner logs.

**Important API boundary:** the proposal-pool scheduler evaluates externally generated candidates; it does not generate RTL. The separate Agent closed-loop controller can invoke an external proposer through a strict JSON command protocol, but no model/provider is bundled and no mutation/recombination operator is claimed.

### Agent RTL patch intake

An external Agent can submit a versioned patch proposal rather than writing directly into the golden RTL. The first adapter is intentionally narrow: one existing file, `tasks/cordic/cordic_rotator.sv`, with a unified diff. The proposal must identify its parent candidate by SHA-256, so a patch created against stale RTL is rejected.

Apply the checked-in behavior-preserving signed-delta refactor:

```bash
sha256sum tasks/cordic/cordic_rotator.sv
python3 -m chiprd.cli apply-patch \
  --patch-proposal examples/patches/signed_delta_refactor.json \
  --output artifacts/agent-patches
```

The command validates the proposal schema, parent hash, safe patch identifier, target path, patch size and unified-diff structure. It then runs `git apply --check` and applies the patch in a temporary workspace containing a copy of the parent RTL. The golden source is never modified. Multi-file, binary, rename, delete, new-file and file-mode patches are rejected. Output must remain inside the repository and an existing patch result is never overwritten.

Subsequent generations may use a previous result as their parent by setting `parent_candidate_path` to `artifacts/agent-patches/<previous-patch-id>/candidate.sv`, carrying forward its candidate SHA-256, and incrementing `generation`. This makes patch lineage incremental without modifying any earlier candidate.

The result directory contains:
- `candidate.sv`: patched RTL, suitable for the sealed experiment runner and search proposal pool.
- `patch.diff`: exact submitted unified diff.
- `patch_manifest.json`: parent, patch and candidate hashes; patcher and Git versions; and a ready-to-copy `search_proposal` entry containing candidate ID, path, parent ID, generation and predicted improvement.

A successful patch application means only that the diff passed intake checks and was applied to the expected parent. It is **not** an RTL correctness or PPA result; the candidate must still pass the normal functional, mutation, formal and equivalence gates. The patch API does not invoke an LLM or provide an adversarial security sandbox.

### Agent closed-loop controller

The `agent-loop` command connects an external Agent adapter to the patch intake and sealed evaluator. It first evaluates the golden baseline, then repeats this cycle within explicit budgets:

1. Send the Agent the current parent candidate ID/path/hash, best feasible candidate, previous experiment summary and failed-gate statuses.
2. Receive one JSON response: either a versioned patch proposal or an explicit stop action.
3. Verify that the proposal's parent ID, path, SHA-256 and generation match the current loop state.
4. Apply the patch through the existing isolated patch intake.
5. Run all six public experiment gates against the new sealed candidate.
6. Feed the result signature, gate outcomes and synthesis proxy back to the Agent on the next iteration.

The Agent is a separate executable. The controller does not bundle or call a particular LLM provider. Use an adapter that reads one JSON object from stdin and writes exactly one JSON object to stdout. The command is passed as a JSON argv array, never through a shell:

```bash
python3 -m chiprd.cli agent-loop \
  --agent-command-json '["python3","examples/agents/mock_agent.py"]' \
  --max-iterations 2 \
  --max-wall-clock-seconds 3600 \
  --max-agent-seconds 180 \
  --max-experiment-seconds 600 \
  --max-candidate-bytes 1048576 \
  --output artifacts/agent-loops/demo \
  --loop-id demo
```

The checked-in `mock_agent.py` is a deterministic CI fixture, not an LLM and not an optimizer. It proposes the behavior-preserving signed-delta patch once, then stops after receiving the experiment feedback. Replace it with a model-backed adapter to run genuine Agent-driven experiments. Pass provider credentials through environment variables; they are not copied into the loop manifest.

Each loop writes a manifest, a hash-chained event ledger, every Agent request/response and stdout/stderr, patch manifests, and sealed experiment directories. The controller enforces iteration, wall-clock, Agent-call, experiment and candidate-size budgets. Agent timeouts terminate the process group. A malformed response or protocol violation fails closed; an invalid patch is recorded and returned as feedback while the current parent remains unchanged. Failed experiments can be used as the next iteration's parent so the Agent can attempt a repair; only candidates passing all six gates are eligible to become the best candidate.

The current objective minimizes Yosys `generic_cell_count` subject to all six public gates passing. This is a generic synthesis proxy, **not** physical area, timing, power or PPA. The controller invokes the supplied Agent command on the local machine; it is not a hostile-code sandbox. Run untrusted adapters and HDL in an isolated environment.

### Model-backed Agent adapter (Phase 8)

The reference adapter at `examples/agents/llm_agent.py` connects the closed-loop controller to an **OpenAI-compatible Chat Completions** endpoint using Python's standard library. Unlike the deterministic fixture, this adapter sends the task specification, full SHA-256 verified parent RTL, best feasible candidate, and previous evaluator feedback to a configured language model.

Configure a local OpenAI-compatible serving endpoint explicitly (no API key is required for loopback):

```bash
export CHIPRD_LLM_BASE_URL=http://127.0.0.1:8000/v1
export CHIPRD_LLM_MODEL=your-local-model
python3 -m chiprd.cli agent-loop \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --max-iterations 3 \
  --max-agent-seconds 120 \
  --max-experiment-seconds 600 \
  --max-wall-clock-seconds 3600 \
  --output artifacts/agent-loops/model-run
```

For a remote OpenAI-compatible provider, use HTTPS and configure `CHIPRD_LLM_BASE_URL`, `CHIPRD_LLM_MODEL`, and `CHIPRD_LLM_API_KEY` in the environment. The default base URL is `https://api.openai.com/v1`. Do not put credentials in command arguments, code, or benchmark artifacts. Optional bounded settings: `CHIPRD_LLM_TIMEOUT_SECONDS` (default 90) and `CHIPRD_LLM_MAX_TOKENS` (default 4096). Plaintext remote HTTP requires explicit `CHIPRD_LLM_ALLOW_HTTP=1`.

The model returns exactly one JSON object: `{"action":"stop","rationale":"..."}` or `{"action":"patch","rtl":"complete SystemVerilog source","rationale":"..."}`. The adapter constructs the single-file unified diff rather than accepting model-authored hunk coordinates. It rejects stale parent hashes, unauthorized paths, unchanged or unterminated RTL, oversized patches, malformed JSON and truncated model responses. Existing patch intake and all six independent public experiment checks remain authoritative.

**Data and security boundaries:** Running this adapter may send the task spec, RTL and feedback to your configured provider, with possible API charges. Use only code and data you may share. The adapter does not log raw model responses or API keys, but the controller retains candidate RTL, patches, and feedback. This is an opt-in model integration, **not a sandbox or proof of autonomous optimization**. CI tests mock HTTP; CI never calls a live LLM or requires credentials.

### Agent benchmark scorecards and token telemetry (Phase 9)

Run `agent-loop` with the model-backed adapter, then generate a reproducible JSON scorecard from its archived report:

```bash
python3 -m chiprd.benchmark_report \
  artifacts/agent-loops/model-run/agent_loop_report.json \
  --output artifacts/agent-loops/model-run/scorecard.json
```

For multiple runs, provide distinct report paths; the resulting comparison retains each run and warns that **equal baseline cell counts do not prove comparable toolchains**. Review source experiment manifests for toolchain, seed and evaluator fingerprints before drawing model comparisons.

The scorecard distinguishes **Agent calls, submitted patches, successfully applied patches, evaluated candidates, fully feasible candidates, and strictly improved candidates**. It reports each of the six public gate pass rates, the baseline and best generic Yosys cell counts, and detailed failure categories.

The OpenAI-compatible adapter captures `prompt_tokens`, `completion_tokens`, and `total_tokens` **only if supplied by the provider's top-level usage field**. These are forwarded as optional, validated metadata through the Agent-loop protocol and archived per iteration. Unreported token usage remains `null` / unknown, never zero. Provider counters are not independently verified. The adapter retains an explicit maximum completion-token cap per model call through `CHIPRD_LLM_MAX_TOKENS`; the full token and dollar cost of a run may not be knowable for providers that omit usage.

This benchmark does **not** independently establish production physical PPA or fully correct functionality. A scorecard alone is not a proof of real autonomous improvement. GitHub CI uses the deterministic fixture and never sends RTL to a provider; real-model comparison requires explicit configuration and cost/privacy approval.

### Evidence boundaries

- Generic cell count is not standard-cell area, timing or power.
- Public mutation tests measure sensitivity to the listed mutations, not broad fault coverage.
- The repository contains public tests, not secret hidden tests.
- The harness is not an adversarial sandbox; execute untrusted HDL only in isolated runners.
- No production PPA, complete formal functional proof, silicon measurement or tape-out assertion is made.

See [CORDIC specification](tasks/cordic/spec.md).
