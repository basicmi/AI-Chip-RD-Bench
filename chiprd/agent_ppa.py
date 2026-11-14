"""Optional timing-constrained Liberty-area objective for the CORDIC Agent loop.

All six sealed experiment gates remain mandatory. Gate-level, ideal-clock timing
does not claim physical timing closure; unavailable PPA never becomes feasible.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ppa, timing
from .ppa_scorecard import score_reports


@dataclass(frozen=True)
class PPAObjective:
    liberty: Path
    tech_lef: Path
    cells_lef: Path
    clock_period_ns: float = 10.0
    io_delay_ns: float = 0.5

    def validate(self) -> dict:
        # Reuse the exact input, timing and boundedness checks used by the
        # standalone timing runner, without doing expensive EDA work.
        period = timing._bounded(self.clock_period_ns, "clock_period_ns", 0.05,
                                 timing.MAX_PERIOD_NS)
        io = timing._bounded(self.io_delay_ns, "io_delay_ns", 0.0, period / 2)
        files = {}
        for name, path, limit in (
            ("liberty", self.liberty, 64 * 1024 * 1024),
            ("tech_lef", self.tech_lef, 16 * 1024 * 1024),
            ("cells_lef", self.cells_lef, 32 * 1024 * 1024),
        ):
            path = Path(path)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                raise ValueError(f"{name} must be a bounded non-symlink regular file")
            files[name] = {"sha256": ppa._sha(path.resolve()), "filename": path.name}
        return {"inputs": files, "clock_period_ns": period, "io_delay_ns": io,
                "objective": "minimum mapped cell area subject to all sealed gates and setup WNS >= 0"}

    def evaluate(self, candidate: Path, reference: Path, output: Path) -> dict:
        """Return objective evidence; fail closed on every exception/error."""
        try:
            report = timing.compare_timing(
                candidate, reference, self.liberty, self.tech_lef, self.cells_lef,
                output, clock_period_ns=self.clock_period_ns,
                io_delay_ns=self.io_delay_ns,
            )
            if report.get("status") != "pass":
                return {"status": "error", "feasible": False,
                        "error": report.get("error", "timing evaluation failed")}
            scorecard = score_reports([output / "timing_report.json"])
            row = scorecard["candidates"][0]
            digest = ppa._sha(candidate)
            if row["candidate_sha256"] != digest:
                raise ValueError("timing evidence candidate SHA-256 mismatch")
            return {
                "status": "pass", "feasible": bool(row["timing_feasible"]),
                "mapped_cell_area": row["mapped_cell_area"],
                "worst_setup_slack_ns": row["worst_setup_slack_ns"],
                "candidate_sha256": digest,
                "timing_report_sha256": row["report_sha256"],
                "improves_reference_area": row["improves_reference_area"],
            }
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            return {"status": "error", "feasible": False,
                    "error": f"{type(exc).__name__}: {exc}"}
