import difflib
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.agent_loop import AgentLoopBudget, parse_agent_command, run_agent_loop
from chiprd.patches import PATCH_TARGET_PATH


OLD_RTL = """module cordic_rotator;
    wire signed [31:0] x_shift = x >>> stage;
    wire signed [31:0] y_shift = y >>> stage;
    wire signed [31:0] x_next = (z >= 0) ? x - y_shift : x + y_shift;
    wire signed [31:0] y_next = (z >= 0) ? y + x_shift : y - x_shift;
    wire signed [31:0] z_next = (z >= 0) ? z - atan_value(stage) : z + atan_value(stage);
endmodule
"""
NEW_RTL = """module cordic_rotator;
    wire signed [31:0] x_shift = x >>> stage;
    wire signed [31:0] y_shift = y >>> stage;
    wire signed [31:0] x_delta = (z >= 0) ? -y_shift : y_shift;
    wire signed [31:0] y_delta = (z >= 0) ? x_shift : -x_shift;
    wire signed [31:0] z_delta = (z >= 0) ? -atan_value(stage) : atan_value(stage);
    wire signed [31:0] x_next = x + x_delta;
    wire signed [31:0] y_next = y + y_delta;
    wire signed [31:0] z_next = z + z_delta;
endmodule
"""


def write_fixture(root: Path) -> list[str]:
    target = root / PATCH_TARGET_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(OLD_RTL, encoding="utf-8")
    patch_text = "".join(difflib.unified_diff(
        OLD_RTL.splitlines(keepends=True), NEW_RTL.splitlines(keepends=True),
        fromfile=f"a/{PATCH_TARGET_PATH}", tofile=f"b/{PATCH_TARGET_PATH}",
    ))
    proposal = {
        "schema_version": "1.0.0", "task_id": "cordic.rotation.q14.v1",
        "patch_id": "signed_delta_test", "parent_candidate_id": "baseline",
        "generation": 1, "parent_candidate_path": PATCH_TARGET_PATH,
        "parent_candidate_sha256": hashlib.sha256(OLD_RTL.encode()).hexdigest(),
        "patch_format": "unified_diff", "patch": patch_text,
        "description": "Test fixture", "predicted_improvement": 0.0,
    }
    template = root / "examples/patches/signed_delta_refactor.json"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(json.dumps(proposal), encoding="utf-8")
    agent = root / "examples/agents/mock_agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "examples/agents/mock_agent.py"
    agent.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return [sys.executable, "examples/agents/mock_agent.py"]


def fake_execution(candidate: Path, cells: int) -> dict:
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    names = ("functional_synthesis", "mutation", "formal",
             "formal_negative_control", "equivalence", "equivalence_selftest")
    return {
        "status": "pass",
        "report": {
            "status": "pass", "candidate_snapshot_intact": True,
            "candidate_sha256": digest,
            "checks": {name: {"status": "pass"} for name in names},
            "result_signature": {
                "functional_synthesis": {"status": "pass",
                    "synthesis": {"generic_cell_count": cells}},
                "mutation": {"status": "pass", "mutation_score": 1.0},
                "formal": {"status": "pass", "proof_status": "proven"},
                "equivalence": {"status": "pass", "proof_status": "proven"},
            },
        },
        "elapsed_seconds": 0.01, "exit_code": 0, "timed_out": False,
    }


@unittest.skipUnless(shutil.which("git"), "git is required for patch application tests")
class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.command = write_fixture(self.root)
        self.output = self.root / "artifacts/agent-loops/test-loop"

    def tearDown(self):
        self.temp.cleanup()

    def run_loop(self, command=None, budget=None):
        def fake_experiment(candidate, output, seed, timeout):
            cells = 1000 if candidate.name == "cordic_rotator.sv" else 950
            return fake_execution(candidate, cells)

        with patch("chiprd.agent_loop.ROOT", self.root), patch(
            "chiprd.patches.ROOT", self.root
        ), patch("chiprd.agent_loop._run_sealed_experiment", side_effect=fake_experiment):
            return run_agent_loop(
                command or self.command, self.output,
                budget=budget or AgentLoopBudget(max_iterations=2),
                seed=123, loop_id="test-loop",
            )

    def test_runs_baseline_patch_then_feeds_feedback_to_agent(self):
        result = self.run_loop()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stop_reason"], "agent_stop")
        self.assertTrue(result["improved_vs_baseline"])
        self.assertEqual(result["best_candidate"]["candidate_id"], "ci_signed_delta_refactor")
        self.assertEqual(result["budget_consumed"]["candidate_experiments"], 2)
        request = json.loads(
            (self.output / "agent-io/iteration-002/request.json").read_text()
        )
        self.assertEqual(request["parent_candidate"]["candidate_id"], "ci_signed_delta_refactor")
        self.assertEqual(request["previous_experiment"]["status"], "pass")
        self.assertEqual(request["previous_experiment"]["generic_cell_count"], 950)
        self.assertEqual((self.root / PATCH_TARGET_PATH).read_text(), OLD_RTL)

    def test_agent_protocol_error_is_recorded(self):
        bad = self.root / "bad_agent.py"
        bad.write_text("print('not-json')\n", encoding="utf-8")
        result = self.run_loop([sys.executable, str(bad)], AgentLoopBudget(max_iterations=1))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stop_reason"], "agent_protocol_error")
        self.assertIn("JSON response", result["iterations"][0]["error"])


    def test_rejects_non_string_action(self):
        bad = self.root / "bad_action_agent.py"
        bad.write_text(
            "import json\\n"
            "print(json.dumps({'schema_version':'1.0.0','action':[]}))\\n",
            encoding="utf-8",
        )
        result = self.run_loop([sys.executable, str(bad)], AgentLoopBudget(max_iterations=1))
        self.assertEqual(result["stop_reason"], "agent_protocol_error")
        self.assertIn("patch or stop", result["iterations"][0]["error"])

    def test_wrong_parent_proposal_is_rejected(self):
        bad = self.root / "wrong_parent_agent.py"
        bad.write_text(
            "import json,sys\n"
            "r=json.load(sys.stdin)\n"
            "p=json.load(open('examples/patches/signed_delta_refactor.json'))\n"
            "p['parent_candidate_id']='not-the-current-parent'\n"
            "print(json.dumps({'schema_version':'1.0.0','action':'patch','proposal':p}))\n",
            encoding="utf-8",
        )
        result = self.run_loop([sys.executable, str(bad)], AgentLoopBudget(max_iterations=1))
        self.assertEqual(result["iterations"][0]["status"], "proposal_rejected")
        self.assertIn("parent_candidate_id", result["iterations"][0]["error"])

    def test_agent_can_stop_without_a_patch(self):
        stop = self.root / "stop_agent.py"
        stop.write_text(
            "import json\n"
            "print(json.dumps({'schema_version':'1.0.0','action':'stop','rationale':'No safe improvement.'}))\n",
            encoding="utf-8",
        )
        result = self.run_loop([sys.executable, str(stop)], AgentLoopBudget(max_iterations=2))
        self.assertEqual(result["stop_reason"], "agent_stop")
        self.assertEqual(result["budget_consumed"]["candidate_experiments"], 1)
        self.assertEqual(result["best_candidate"]["candidate_id"], "baseline")

    def test_refuses_to_overwrite_an_existing_loop(self):
        self.run_loop()
        with self.assertRaises(FileExistsError):
            self.run_loop()

    def test_rejects_output_outside_repository(self):
        with tempfile.TemporaryDirectory() as outside:
            with patch("chiprd.agent_loop.ROOT", self.root):
                with self.assertRaisesRegex(ValueError, "inside the repository root"):
                    run_agent_loop(self.command, Path(outside) / "loop")

    def test_validates_agent_command_json(self):
        with self.assertRaisesRegex(ValueError, "JSON array"):
            parse_agent_command("python examples/agents/mock_agent.py")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            parse_agent_command("[]")
        self.assertEqual(
            parse_agent_command('["python3","agent.py","--mode","test"]'),
            ["python3", "agent.py", "--mode", "test"],
        )

    def test_validates_loop_budgets(self):
        with self.assertRaisesRegex(ValueError, "max_iterations"):
            AgentLoopBudget(max_iterations=0).validate()
        with self.assertRaisesRegex(ValueError, "max_agent_seconds"):
            AgentLoopBudget(max_agent_seconds=float("inf")).validate()


if __name__ == "__main__":
    unittest.main()
