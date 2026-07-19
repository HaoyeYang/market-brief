import json
import tempfile
import unittest
from pathlib import Path

from recover_workflow import find_completed_result, recover_run_json


RESULT = "report\n<<RANK_JSON_BEGIN>>\n{}\n<<RANK_JSON_END>>\n"


class WorkflowRecoveryTests(unittest.TestCase):
    def _write_workflow(self, root: Path, name: str, **changes):
        path = root / "project" / "session" / "workflows" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "workflowName": "market-brief",
            "status": "completed",
            "startTime": 2_000_000,
            "args": json.dumps({"dataPath": "/tmp/data.json", "historyPath": "/tmp/history.json"}),
            "result": RESULT,
            "runId": name,
            "durationMs": 123,
            "totalTokens": 456,
            "totalToolCalls": 7,
        }
        record.update(changes)
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def test_requires_exact_paths_time_status_and_sentinels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_workflow(root, "old", startTime=999_000)
            self._write_workflow(root, "wrong", args=json.dumps({
                "dataPath": "/tmp/other.json", "historyPath": "/tmp/history.json"
            }))
            self._write_workflow(root, "failed", status="failed")
            self._write_workflow(root, "truncated", result="report only")
            expected = self._write_workflow(root, "good", startTime=2_100_000)

            match = find_completed_result(
                root,
                data_path="/tmp/data.json",
                history_path="/tmp/history.json",
                started_after=1_000,
            )
            self.assertIsNotNone(match)
            self.assertEqual(match[0], expected)

    def test_recovery_preserves_cost_and_marks_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_path = self._write_workflow(root, "good")
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            run_json = root / "run.json"
            run_json.write_text(json.dumps({
                "is_error": True,
                "result": None,
                "total_cost_usd": 1.25,
                "permission_denials": [],
            }), encoding="utf-8")

            recover_run_json(run_json, workflow_path, workflow)
            recovered = json.loads(run_json.read_text(encoding="utf-8"))
            self.assertFalse(recovered["is_error"])
            self.assertTrue(recovered["outer_relay_is_error"])
            self.assertEqual(recovered["total_cost_usd"], 1.25)
            self.assertEqual(recovered["result"], RESULT)
            self.assertEqual(recovered["recovered_from_workflow_journal"]["run_id"], "good")


if __name__ == "__main__":
    unittest.main()
