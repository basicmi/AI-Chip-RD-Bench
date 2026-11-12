# AI-Chip-RD-Bench

A reproducible, evidence-gated benchmark for **AI autonomous chip R&D**.

## Phase 0: CORDIC Golden Task

This repository starts with a deliberately narrow, independently checked fixed-point CORDIC rotation task. It provides an RTL example, analytic mathematical oracle, Verilator testbench, Yosys generic synthesis adapter, structured evidence files, CI and Docker.

**Scope:** functional simulation + generic synthesis only. The generic cell count is not physical PPA or tape-out sign-off. Formal checking, mutation suites, private hidden-test hosting, actual PDK/SDC/OpenROAD flows and Agent search are future milestones; they are not claimed as implemented here.

### Quickstart (Ubuntu 22.04/24.04)

```bash
sudo apt-get update
sudo apt-get install -y verilator yosys make g++ python3
python3 -m unittest discover -s tests -v
python3 -m chiprd.cli smoke
python3 -m chiprd.cli all --candidate tasks/cordic/cordic_rotator.sv --output artifacts/cordic
cat artifacts/cordic/report.json
```

A passing `all` command must have `"status": "pass"` plus both functional and synthesis sections. Missing tools cause explicit **error**, never a fake pass. A functional failure prevents the synthesis stage in `all` mode.

### Docker

```bash
docker build -t ai-chip-rd-bench:phase0 .
docker run --rm -v "$PWD/artifacts:/work/artifacts" ai-chip-rd-bench:phase0
```

### Evidence

`artifacts/cordic/report.json` includes task ID, SHA-256 of candidate, tool versions, seed, functional failures and generic cell statistics. Compile, simulation and Yosys logs are saved alongside it. Artifacts are ignored by Git and uploaded by CI.

### Verification and trust model

- The reference model is analytic sine/cosine, independent of the shift-add RTL algorithm.
- The evaluator owns the testbench and computes an independent score from simulator output.
- Deterministic vectors cover zero, both signs, near-zero and angular boundaries.
- The current repository hosts *public* tests only. A public CI file is **not a secret hidden test**. Proper held-out evaluation requires separate, access-controlled infrastructure.
- This is an engineering harness, **not an adversarial sandbox**; run untrusted HDL/tool commands inside containers or a dedicated runner.
- No production PPA, formal proof, silicon measurement, or tape-out assertion is made.

### Planned next steps

1. Formal / equivalence adapters (SymbiYosys / EQY) with explicit bounded/proven statuses.
2. Mutation-based verification quality checks and stronger held-out validation.
3. Candidate sealing, evaluator manifests, schema validation and budget ledger.
4. OpenROAD reproducible PPA on a fixed public platform and constraints.
5. Model-independent Agent API and fixed-budget search baselines.

See [CORDIC specification](tasks/cordic/spec.md).
