# AI-Chip-RD-Bench

A reproducible, evidence-gated benchmark for **AI autonomous chip R&D**.

## Phase 0: CORDIC Golden Task

This repository starts with a deliberately narrow, independently checked fixed-point CORDIC rotation task. It provides an RTL example, analytic mathematical oracle, Verilator testbench, Yosys generic synthesis adapter, mutation campaign, structured evidence files, CI and Docker.

**Scope:** functional simulation, mutation sensitivity and generic synthesis only. The generic cell count is not physical PPA or tape-out sign-off. Formal checking, private hidden-test hosting, actual PDK/SDC/OpenROAD flows and Agent search are future milestones; they are not claimed as implemented here.

### Quickstart (Ubuntu 22.04/24.04)

```bash
sudo apt-get update
sudo apt-get install -y verilator yosys make g++ python3
python3 -m unittest discover -s tests -v
python3 -m chiprd.cli smoke
python3 -m chiprd.cli all --candidate tasks/cordic/cordic_rotator.sv --output artifacts/cordic
python3 -m chiprd.cli mutation --candidate tasks/cordic/cordic_rotator.sv --output artifacts/mutation
cat artifacts/cordic/report.json
cat artifacts/mutation/mutation_report.json
```

A passing `all` command must have `"status": "pass"` plus both functional and synthesis sections. Missing tools cause explicit **error**, never a fake pass. A functional failure prevents the synthesis stage in `all` mode.

The mutation campaign first verifies an immutable candidate snapshot. It then injects five documented functional faults (gain initialization, rotation direction, arctangent table, output mapping and valid handshake). The campaign passes only when the baseline passes and **all five mutants are rejected** by the independent functional evaluator. The mutation score is killed mutants divided by total mutants. A compile or simulation failure counts as a killed mutant; an evaluator/tool error is reported separately and does not count as a kill.

### Docker

```bash
docker build -t ai-chip-rd-bench:phase0 .
docker run --rm -v "$PWD/artifacts:/work/artifacts" ai-chip-rd-bench:phase0
```

### Evidence

`artifacts/cordic/report.json` includes task ID, SHA-256 of candidate, tool versions, seed, functional failures and generic cell statistics. Compile, simulation and Yosys logs are saved alongside it.

`artifacts/mutation/` contains the sealed candidate snapshot, a manifest binding the candidate/testbench/oracle hashes and seed, baseline evidence, each generated mutant, per-mutant Verilator logs, and `mutation_report.json`. This makes the public mutation campaign reproducible; it does not make the public tests secret.

### Verification and trust model

- The reference model is analytic sine/cosine, independent of the shift-add RTL algorithm.
- The evaluator owns the testbench and computes an independent score from simulator output.
- Deterministic vectors cover zero, both signs, near-zero and angular boundaries.
- The five public mutations are a small, explicit sensitivity check, not comprehensive fault coverage or a replacement for held-out tests.
- The current repository hosts *public* tests only. A public CI file is **not a secret hidden test**. Proper held-out evaluation requires separate, access-controlled infrastructure.
- This is an engineering harness, **not an adversarial sandbox**; run untrusted HDL/tool commands inside containers or a dedicated runner.
- No production PPA, formal proof, silicon measurement, or tape-out assertion is made.

### Planned next steps

1. Formal / equivalence adapters (SymbiYosys / EQY) with explicit bounded/proven statuses.
2. Stronger randomized and boundary-focused validation plus private held-out evaluator design.
3. Candidate sealing, evaluator manifests, schema validation and budget ledger.
4. OpenROAD reproducible PPA on a fixed public platform and constraints.
5. Model-independent Agent API and fixed-budget search baselines.

See [CORDIC specification](tasks/cordic/spec.md).
