# AI-Chip-RD-Bench

A reproducible, evidence-gated benchmark for **AI autonomous chip R&D**.

## Phase 0: CORDIC Golden Task

This repository provides a fixed-point CORDIC task, reference RTL, analytic mathematical oracle, Verilator testbench, Yosys generic synthesis adapter, simulation mutation campaign, protocol-level formal checks, structured evidence and CI.

**Scope:** functional simulation, mutation sensitivity, protocol-level formal proof and generic synthesis only. Generic cell count is not physical PPA. Formal numerical equivalence, private held-out tests, actual PDK/SDC/OpenROAD flows and Agent search remain future milestones.

### Quickstart (Ubuntu 22.04/24.04)

```bash
sudo apt-get update
sudo apt-get install -y verilator yosys z3 git make python3
python3 -m pip install click
git clone --depth 1 https://github.com/YosysHQ/sby.git /tmp/sby
make -C /tmp/sby install PREFIX="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"

python3 -m unittest discover -s tests -v
python3 -m chiprd.cli all --candidate tasks/cordic/cordic_rotator.sv --output artifacts/ci
python3 -m chiprd.cli mutation --candidate tasks/cordic/cordic_rotator.sv --output artifacts/mutation
python3 -m chiprd.cli formal --candidate tasks/cordic/cordic_rotator.sv --output artifacts/formal
python3 -m chiprd.cli formal-negative-control --candidate tasks/cordic/cordic_rotator.sv --output artifacts/formal-negative-control
```

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

### Evidence

- `artifacts/ci/report.json`: candidate hash, tool versions, functional results and generic synthesis statistics.
- `artifacts/mutation/`: sealed candidate snapshot, manifest, mutants and per-mutant logs.
- `artifacts/formal/formal_report.json`: candidate/harness hashes, tool versions, proof mode/depth, properties and proof status.
- `artifacts/formal-negative-control/`: baseline proof and deliberately failing mutant proof.

### Trust model

The repository contains public tests, not secret hidden tests. The harness is not an adversarial sandbox; execute untrusted HDL only in isolated runners. No production PPA, complete formal functional proof, silicon measurement or tape-out assertion is made.

### Planned next steps

1. Strengthen randomized and boundary-focused validation plus private held-out evaluator design.
2. Add sequential equivalence (EQY) for behavior-preserving RTL transformations.
3. Candidate sealing, evaluator manifests, schema validation and budget ledger.
4. OpenROAD reproducible PPA on a fixed public platform and constraints.
5. Model-independent Agent API and fixed-budget search baselines.

See [CORDIC specification](tasks/cordic/spec.md).
