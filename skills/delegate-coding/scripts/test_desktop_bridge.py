"""Offline delivery/protocol checks. No UI control, network, or live workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import delegate as d
import desktop_bridge as b


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="desktop-bridge-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.board = self.root / "board"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        d.initialize(self.board)
        spec = self.root / "spec.json"
        d.write_json(spec, {"title": "File roundtrip", "goal": "Write a task ID into echo.txt",
            "context_files": [], "allowed_paths": ["echo.txt"], "constraints": [],
            "acceptance": ["echo.txt contains this task ID"]})
        self.created = d.create(self.board, argparse.Namespace(route="desktop", executor="workbuddy",
            workspace=str(self.workspace), task=str(spec), allow_no_git=True, api_root=None))
        self.task_id = self.created["task_id"]
        self.job = Path(self.created["task_directory"])

    def begin(self):
        return b.begin(self.board, self.task_id, "WorkBuddy", "window-17",
                       "Bridge trial", "Observed window 17 and conversation Bridge trial; composer empty")

    def attempt(self):
        b.prepare(self.board, self.task_id)
        return self.begin()["attempt"]["attempt_id"]

    def report(self):
        claim = d.claim(self.board, self.task_id, "workbuddy")
        (self.workspace / "echo.txt").write_text(self.task_id, encoding="utf-8")
        report = {"schema_version": 1, "task_id": self.task_id, "claim_token": claim["claim_token"],
            "executor": "workbuddy", "status": "completed", "summary": "Wrote task ID",
            "changed_files": ["echo.txt"], "checks": [], "issues": []}
        d.submit(self.board, self.task_id, report)
        return report

    def test_prepare_is_idempotent_and_does_not_send_or_claim(self):
        first = b.prepare(self.board, self.task_id)
        before = (self.job / b.DELIVERY_FILE).read_bytes()
        second = b.prepare(self.board, self.task_id)
        self.assertEqual(first, second)
        self.assertEqual(before, (self.job / b.DELIVERY_FILE).read_bytes())
        self.assertEqual(set(first["bound_sha256"]), set(b.BOUND_FILES))
        self.assertIn(str(self.job / "TASK.md"), first["handoff_text"])
        self.assertEqual(d.task_state(self.job)[1]["state"], "queued")

    def test_concurrent_begin_has_one_winner(self):
        b.prepare(self.board, self.task_id)
        def attempt(_):
            try:
                return self.begin()["attempt"]["attempt_id"]
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=6) as workers:
            attempts = list(workers.map(attempt, range(6)))
        winners = [value for value in attempts if value]
        self.assertEqual(len(winners), 1)
        value = d.read_json(self.job / b.DELIVERY_FILE)
        self.assertEqual(value["attempt"]["attempt_id"], winners[0])
        self.assertEqual([event["event"] for event in value["history"]], ["prepared", "began"])

    def test_every_bound_file_drift_blocks_begin_and_is_visible_in_status(self):
        b.prepare(self.board, self.task_id)
        for name in b.BOUND_FILES:
            path = self.job / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n")
                with self.subTest(file=name), self.assertRaises(ValueError):
                    self.begin()
                result = b.status(self.board, self.task_id)
                self.assertFalse(result["binding_matches"])
                self.assertEqual(result["next_action"], "inspect_binding_change_do_not_send")
            finally:
                path.write_bytes(original)
        self.assertEqual(self.begin()["status"], "sending")

    def test_uncertain_blocks_automatic_resend(self):
        attempt_id = self.attempt()
        b.record(self.board, self.task_id, attempt_id, "uncertain", "UI tool disconnected after input")
        with self.assertRaises(ValueError):
            self.begin()
        with self.assertRaises(ValueError):
            b.prepare(self.board, self.task_id)
        self.assertEqual(b.status(self.board, self.task_id)["next_action"],
                         "inspect_target_and_task_do_not_resend")

    def test_sent_is_not_acknowledged_or_accepted(self):
        attempt_id = self.attempt()
        b.record(self.board, self.task_id, attempt_id, "sent_verified", "Observed task ID in outgoing message")
        result = b.status(self.board, self.task_id)
        self.assertEqual(result["execution_state"], "queued")
        self.assertFalse(result["worker_acknowledged"])
        self.assertFalse(result["ready_to_collect"])
        self.assertFalse(result["accepted_by_gpt"])
        self.assertEqual(result["next_action"], "wait_for_claim_do_not_resend")
        with self.assertRaises(ValueError):
            b.release_unsent(self.board, self.task_id, attempt_id, "Claim has not arrived", True)

    def test_claim_report_and_review_use_existing_protocol(self):
        attempt_id = self.attempt()
        claim = d.claim(self.board, self.task_id, "workbuddy")
        # A quick worker can claim before the sender records its UI observation.
        b.record(self.board, self.task_id, attempt_id, "sent_verified", "Outgoing message observed")
        result = b.status(self.board, self.task_id)
        self.assertTrue(result["worker_acknowledged"])
        self.assertEqual(result["next_action"], "wait_for_report")
        report = {"schema_version": 1, "task_id": self.task_id, "claim_token": claim["claim_token"],
            "executor": "workbuddy", "status": "completed", "summary": "Offline result",
            "changed_files": [], "checks": [], "issues": []}
        d.submit(self.board, self.task_id, report)
        result = b.status(self.board, self.task_id)
        self.assertTrue(result["ready_to_collect"])
        self.assertEqual(result["next_action"], "collect_and_review")
        self.assertFalse(d.collect(self.board, self.task_id)["accepted_by_gpt"])
        d.review(self.board, argparse.Namespace(task_id=self.task_id, decision="accept",
            note="Offline protocol verified; no implementation change", worker_stopped=False))
        result = b.status(self.board, self.task_id)
        self.assertTrue(result["accepted_by_gpt"])
        self.assertEqual(result["next_action"], "done")
        with self.assertRaises(ValueError):
            self.begin()

    def test_wrong_attempt_cannot_record_or_release(self):
        self.attempt()
        before = (self.job / b.DELIVERY_FILE).read_bytes()
        with self.assertRaises(ValueError):
            b.record(self.board, self.task_id, "wrong", "sent_verified", "Observed message")
        with self.assertRaises(ValueError):
            b.release_unsent(self.board, self.task_id, "wrong", "Confirmed no send", True)
        self.assertEqual(before, (self.job / b.DELIVERY_FILE).read_bytes())

    def test_release_requires_confirmation_and_preserves_old_attempt(self):
        attempt_id = self.attempt()
        b.record(self.board, self.task_id, attempt_id, "uncertain", "Interrupted before outcome")
        with self.assertRaises(ValueError):
            b.release_unsent(self.board, self.task_id, attempt_id, "Send was not pressed")
        with self.assertRaises(ValueError):
            b.release_unsent(self.board, self.task_id, attempt_id, " ", True)
        b.release_unsent(self.board, self.task_id, attempt_id,
                         "Observed unchanged empty conversation; unsent draft cleared before any send", True)
        new = self.begin()
        self.assertNotEqual(new["attempt"]["attempt_id"], attempt_id)
        released = [event for event in new["history"] if event["event"] == "released_unsent"]
        self.assertEqual(released[0]["previous_attempt"]["attempt_id"], attempt_id)
        self.assertEqual(released[0]["previous_attempt"]["outcome"], "uncertain")
        with self.assertRaises(ValueError):
            b.record(self.board, self.task_id, attempt_id, "sent_verified", "Old observation")

    def test_cancelled_task_cannot_send_or_be_revived(self):
        b.prepare(self.board, self.task_id)
        d.cancel(self.board, self.task_id)
        with self.assertRaises(ValueError):
            self.begin()
        with self.assertRaises(ValueError):
            b.prepare(self.board, self.task_id)
        result = b.status(self.board, self.task_id)
        self.assertEqual(result["execution_state"], "cancelled")
        self.assertEqual(result["next_action"], "task_closed")

    def test_record_after_cancellation_only_preserves_delivery_evidence(self):
        attempt_id = self.attempt()
        d.cancel(self.board, self.task_id)
        b.record(self.board, self.task_id, attempt_id, "uncertain", "Cancellation raced with UI action")
        self.assertEqual(d.task_state(self.job)[1]["state"], "cancelled")
        with self.assertRaises(ValueError):
            b.release_unsent(self.board, self.task_id, attempt_id, "Observed no send", True)
        with self.assertRaises(ValueError):
            self.begin()

    def test_claimed_cancellation_preserves_workspace_reservation(self):
        self.attempt()
        d.claim(self.board, self.task_id, "workbuddy")
        d.cancel(self.board, self.task_id)
        result = b.status(self.board, self.task_id)
        self.assertEqual(result["next_action"], "confirm_worker_stopped_then_review")
        self.assertTrue(d.workspace_lock(self.board, self.workspace).exists())

    def test_api_task_is_rejected_without_delivery_artifact(self):
        task = d.read_json(self.job / "task.json")
        task["route"] = "api"
        d.write_json(self.job / "task.json", task)
        with self.assertRaises(ValueError):
            b.prepare(self.board, self.task_id)
        self.assertFalse((self.job / b.DELIVERY_FILE).exists())

    def test_status_is_readonly_and_validates_report_identity(self):
        self.attempt()
        self.report()
        before_delivery = (self.job / b.DELIVERY_FILE).read_bytes()
        before_state = (self.job / "state.json").read_bytes()
        self.assertTrue(b.status(self.board, self.task_id)["report_valid"])
        self.assertEqual(before_delivery, (self.job / b.DELIVERY_FILE).read_bytes())
        self.assertEqual(before_state, (self.job / "state.json").read_bytes())
        report = d.read_json(self.job / "RESULT.json")
        report["task_id"] = "task-wrong"
        d.write_json(self.job / "RESULT.json", report)
        result = b.status(self.board, self.task_id)
        self.assertFalse(result["ready_to_collect"])
        self.assertEqual(result["next_action"], "inspect_invalid_report")

    def test_target_and_evidence_are_required_and_cli_failure_is_nonzero(self):
        b.prepare(self.board, self.task_id)
        with self.assertRaises(ValueError):
            b.begin(self.board, self.task_id, "WorkBuddy", "", "Chat", "Observed")
        with contextlib.redirect_stderr(io.StringIO()):
            code = b.main(["--board", str(self.board), "record", "--task-id", self.task_id,
                           "--attempt-id", "wrong", "--outcome", "sent_verified", "--evidence", "Observed"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
