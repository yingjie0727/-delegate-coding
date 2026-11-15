"""Offline behavior checks: no desktop dispatch, network or real credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import delegate as d


FAKE_CLI = '''import json
from pathlib import Path
import subprocess
import sys
import time

if "--version" in sys.argv[1:]:
    print("fake-codebuddy 0.0.0")
    raise SystemExit(0)
here = Path(__file__).resolve().parent
payload = json.loads((here / "fake-config.json").read_text(encoding="utf-8-sig"))
Path(payload["argv_file"]).write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding="utf-8")
time.sleep(payload.get("sleep", 0))
if payload.get("silent"):
    raise SystemExit(payload.get("exit", 0))
claim = subprocess.run(payload["claim"], capture_output=True, text=True, check=True)
token = json.loads(claim.stdout)["claim_token"]
for name, text in payload.get("writes", {}).items():
    (Path(payload["workspace"]) / name).write_text(text, encoding="utf-8")
report = {"schema_version": 1, "task_id": payload["task_id"], "claim_token": token,
          "executor": payload["executor"], "status": "completed",
          "summary": "Offline fake CLI implemented the bounded task",
          "changed_files": sorted(payload.get("writes", {})), "checks": [], "issues": []}
Path(payload["report_file"]).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
subprocess.run(payload["report"], check=True, capture_output=True)
raise SystemExit(payload.get("exit", 0))
'''


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delegation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.board = self.root / "task board"
        self.workspace = self.root / "implementation"
        self.workspace.mkdir()
        d.initialize(self.board)
        self.git("init", "-q")
        self.git("config", "user.email", "offline-test@example.invalid")
        self.git("config", "user.name", "Offline test")
        (self.workspace / "main.txt").write_text("before\n", encoding="utf-8")
        (self.workspace / "other.txt").write_text("untouched\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.spec = self.root / "spec.json"
        d.write_json(self.spec, {"title": "Small edit", "goal": "Change main.txt only",
            "allowed_paths": ["main.txt"], "acceptance": ["main.txt contains after"],
            "constraints": ["Do not change unrelated files"], "context_files": ["main.txt"]})
        self.profile = self.root / "api-profile"
        self.profile.mkdir()
        d.write_json(self.profile / "worker.json", {"schema_version": 1,
            "base_url": "https://responses.example.invalid/v1", "model": "example-code",
            "context_window": 128000, "key_env": "EXAMPLE_API_KEY", "provider_name": "Offline example",
            "windows_sandbox": "elevated"})
        self.fake_dir = self.root / "fake cli"
        self.fake_dir.mkdir()
        (self.fake_dir / "fake_cli.py").write_text(FAKE_CLI, encoding="utf-8")
        self.argv_file = self.fake_dir / "argv.json"
        self.fake_config = self.fake_dir / "fake-config.json"
        self.fake_cli = self.make_launcher()

    def make_launcher(self):
        if os.name == "nt":
            path = self.fake_dir / "codebuddy.cmd"
            path.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_cli.py" %*\r\n', encoding="utf-8")
        else:
            path = self.fake_dir / "codebuddy"
            path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_cli.py" "$@"\n', encoding="utf-8")
            path.chmod(0o755)
        return path

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.workspace), *args], check=True,
                              capture_output=True, timeout=20).stdout

    def create(self, route="desktop", executor="workbuddy", **overrides):
        options = dict(route=route, executor=executor, workspace=str(self.workspace),
                       task=str(self.spec), allow_no_git=False,
                       api_root=str(self.profile) if route == "api" else None,
                       cli_path=None, model=None)
        options.update(overrides)
        return d.create(self.board, argparse.Namespace(**options))

    def ready(self, **overrides):
        created = self.create(**overrides)
        state = d.claim(self.board, created["task_id"], created["executor"])
        report = {"schema_version": 1, "task_id": created["task_id"],
            "executor": created["executor"], "claim_token": state["claim_token"],
            "status": "completed", "summary": "Implementation ready for inspection",
            "changed_files": [], "checks": [], "issues": []}
        return created, report

    def review(self, task, decision="accept", worker_stopped=False):
        return d.review(self.board, argparse.Namespace(task_id=task["task_id"], decision=decision,
            note="Independently checked in this offline test", worker_stopped=worker_stopped))

    def run_api(self, created, runner, root=None):
        import api_worker
        args = argparse.Namespace(task_id=created["task_id"], api_root=str(root or self.profile),
                                  timeout=5, codex=None)
        with patch.object(api_worker, "run", runner):
            return d.run_api(self.board, args)

    def fake_success(self, root, args):
        output = Path(args.output_dir)
        output.mkdir()
        (self.workspace / "main.txt").write_text("after\n", encoding="utf-8")
        (output / "final.md").write_text("Changed main.txt; GPT must check it.", encoding="utf-8")
        d.write_json(output / "result.json", {"status": "ready_for_review", "worker_exit_code": 0,
            "events": {"completed": True, "failed": False}, "changed_files": ["main.txt"]})
        return 0

    def test_desktop_handoff_is_explicit_and_reusable(self):
        for name in ("workbuddy", "zcode", "generic"):
            created = self.create(executor=name)
            self.assertFalse(created["automatic_dispatch"])
            job = Path(created["task_directory"])
            self.assertIn("claim", (job / "TASK.md").read_text(encoding="utf-8"))
            self.assertIn("report", (job / "TASK.md").read_text(encoding="utf-8"))
            self.assertIn(str(job / "TASK.md"), (job / "HANDOFF.txt").read_text(encoding="utf-8"))
            self.assertEqual(d.cancel(self.board, created["task_id"])["state"], "cancelled")

    def test_desktop_controller_runs_from_board_without_skill_folder_access(self):
        task = self.create()
        local = Path(task["task_directory"]) / "controller.py"
        self.assertEqual(local.read_bytes(), Path(d.__file__).read_bytes())
        proc = subprocess.run([sys.executable, "-B", str(local), "--board", str(self.board),
                               "claim", "--task-id", task["task_id"], "--executor", "workbuddy"],
                              capture_output=True, check=True, timeout=20)
        self.assertEqual(json.loads(proc.stdout)["state"], "claimed")

    def test_custom_assistant_registration_and_no_override(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.main(["--board", str(self.board), "register", "--id", "local-ide", "--display-name", "Local IDE"]), 0)
        item = d.registry(self.board)["local-ide"]
        self.assertFalse(item["auto_dispatch"])
        self.assertEqual(self.create(executor="local-ide")["executor"], "local-ide")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(d.main(["--board", str(self.board), "register", "--id", "zcode", "--display-name", "Replacement"]), 2)

    def test_only_one_unfinished_task_per_board_workspace(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.create(executor="zcode")
        d.cancel(self.board, task["task_id"])
        self.assertEqual(self.create(executor="zcode")["state"], "queued")

    def test_dirty_workspace_and_nested_board_rejected(self):
        (self.workspace / "main.txt").write_text("already edited", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.create()
        nested = self.workspace / "board"
        d.initialize(nested)
        with self.assertRaises(ValueError):
            d.create(nested, argparse.Namespace(route="desktop", executor="workbuddy",
                workspace=str(self.workspace), task=str(self.spec), allow_no_git=True, api_root=None))

    def test_non_git_is_explicit_and_reports_weaker_evidence(self):
        files = self.root / "files"
        files.mkdir()
        with self.assertRaises(ValueError):
            self.create(workspace=str(files))
        task, report = self.ready(workspace=str(files), allow_no_git=True)
        d.submit(self.board, task["task_id"], report)
        collected = d.collect(self.board, task["task_id"])
        self.assertFalse(collected["observations"]["git_verified"])
        self.assertTrue(collected["observations"]["reported_changes_only"])

    def test_claim_checks_executor_and_cannot_repeat(self):
        task = self.create()
        with self.assertRaises(ValueError):
            d.claim(self.board, task["task_id"], "zcode")
        d.claim(self.board, task["task_id"], "workbuddy")
        with self.assertRaises(ValueError):
            d.claim(self.board, task["task_id"], "workbuddy")

    def configure_fake(self, task, writes=None, silent=False, sleep=0):
        task_dir = Path(task["task_directory"])
        config = {"argv_file": str(self.argv_file), "workspace": str(self.workspace),
            "task_id": task["task_id"], "executor": task["executor"],
            "report_file": str(task_dir / "worker-report.json"),
            "claim": [sys.executable, str(task_dir / "controller.py"), "--board", str(self.board),
                      "claim", "--task-id", task["task_id"], "--executor", task["executor"]],
            "report": [sys.executable, str(task_dir / "controller.py"), "--board", str(self.board),
                       "report", "--task-id", task["task_id"], "--file", str(task_dir / "worker-report.json")],
            "writes": {"main.txt": "after\n"} if writes is None else writes,
            "silent": silent, "sleep": sleep}
        d.write_json(self.fake_config, config)

    def run_cli(self, task, **overrides):
        options = dict(task_id=task["task_id"], executable=None, timeout=20, max_turns=0, dry_run=False)
        options.update(overrides)
        return d.run_cli(self.board, argparse.Namespace(**options))

    def test_competing_claims_have_one_winner(self):
        task = self.create()
        def attempt(_):
            try:
                return d.claim(self.board, task["task_id"], "workbuddy")["state"]
            except ValueError:
                return "refused"
        with ThreadPoolExecutor(max_workers=4) as pool:
            states = list(pool.map(attempt, range(4)))
        self.assertEqual(states.count("claimed"), 1)

    def test_report_identity_and_claim_token_required(self):
        task, report = self.ready()
        for field in ("task_id", "executor", "claim_token"):
            bad = {**report, field: "wrong"}
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.submit(self.board, task["task_id"], bad)
        self.assertEqual(d.submit(self.board, task["task_id"], report)["state"], "ready_for_review")
        self.assertEqual(d.submit(self.board, task["task_id"], report)["state"], "ready_for_review")

    def test_reported_completion_is_not_acceptance(self):
        task, report = self.ready()
        d.submit(self.board, task["task_id"], report)
        self.assertFalse(d.collect(self.board, task["task_id"])["accepted_by_gpt"])
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.review(task)["state"], "accepted")
        self.assertEqual(self.create()["state"], "queued")

    def test_actual_scope_violations_ignore_worker_claims(self):
        task, report = self.ready()
        (self.workspace / "other.txt").write_text("unexpected", encoding="utf-8")
        (self.workspace / "unreported.txt").write_text("new", encoding="utf-8")
        d.submit(self.board, task["task_id"], report)
        observations = d.collect(self.board, task["task_id"])["observations"]
        self.assertEqual(observations["scope_violations"], ["other.txt", "unreported.txt"])
        self.assertFalse(observations["tests_independently_verified"])
        with self.assertRaises(ValueError):
            self.review(task)
        self.assertEqual(self.review(task, "reject")["state"], "rejected")

    def test_staging_is_detected_before_acceptance(self):
        task, report = self.ready()
        (self.workspace / "main.txt").write_text("after", encoding="utf-8")
        self.git("add", "main.txt")
        d.submit(self.board, task["task_id"], report)
        self.assertEqual(d.collect(self.board, task["task_id"])["observations"]["staged_files"], ["main.txt"])
        with self.assertRaises(ValueError):
            self.review(task)

    def test_commit_is_detected_even_if_tree_clean(self):
        task, report = self.ready()
        (self.workspace / "main.txt").write_text("after", encoding="utf-8")
        self.git("add", "main.txt")
        self.git("commit", "-qm", "unapproved commit")
        d.submit(self.board, task["task_id"], report)
        self.assertTrue(d.collect(self.board, task["task_id"])["observations"]["head_changed"])
        with self.assertRaises(ValueError):
            self.review(task)

    def test_review_without_report_is_not_accepted(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.review(task)

    def test_failed_worker_cannot_be_accepted(self):
        task, report = self.ready()
        report["status"] = "blocked"
        d.submit(self.board, task["task_id"], report)
        with self.assertRaises(ValueError):
            self.review(task)

    def test_cancel_claimed_retains_reservation_until_stopped(self):
        task, _ = self.ready()
        self.assertEqual(d.cancel(self.board, task["task_id"])["state"], "cancel_requested")
        with self.assertRaises(ValueError):
            self.create()
        with self.assertRaises(ValueError):
            self.review(task, "reject")
        self.assertEqual(self.review(task, "reject", True)["state"], "rejected")
        self.assertEqual(self.create()["state"], "queued")

    def test_accepted_evidence_is_frozen(self):
        task, report = self.ready()
        (self.workspace / "main.txt").write_text("after\n", encoding="utf-8")
        d.submit(self.board, task["task_id"], report)
        self.review(task)
        job = Path(task["task_directory"])
        saved_patch = (job / "changes.patch").read_bytes()
        (self.workspace / "other.txt").write_text("a later task", encoding="utf-8")
        result = d.collect(self.board, task["task_id"])
        self.assertEqual(result["observations"]["actual_changed_files"], ["main.txt"])
        self.assertEqual((job / "changes.patch").read_bytes(), saved_patch)

    def test_report_commands_are_data_not_executed(self):
        task, report = self.ready()
        target = self.root / "should-not-exist.txt"
        report["checks"] = [{"command": f"write {target}", "outcome": "passed", "details": "untrusted claim"}]
        d.submit(self.board, task["task_id"], report)
        d.collect(self.board, task["task_id"])
        self.assertFalse(target.exists())

    def test_api_profile_required_and_external(self):
        with self.assertRaises(ValueError):
            self.create(route="api", executor="example-api", api_root=None)
        nested = self.workspace / "profile"
        nested.mkdir()
        shutil.copyfile(self.profile / "worker.json", nested / "worker.json")
        with self.assertRaises(ValueError):
            self.create(route="api", executor="example-api", api_root=str(nested))

    def test_api_profile_and_model_cannot_switch(self):
        task = self.create(route="api", executor="example-api")
        other = self.root / "other-profile"
        shutil.copytree(self.profile, other)
        with self.assertRaises(ValueError):
            self.run_api(task, self.fake_success, other)
        settings = d.read_json(self.profile / "worker.json")
        settings["model"] = "different-model"
        d.write_json(self.profile / "worker.json", settings)
        with self.assertRaises(ValueError):
            self.run_api(task, self.fake_success)
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_api_valid_completion_uses_common_review_protocol(self):
        task = self.create(route="api", executor="example-api")
        result = self.run_api(task, self.fake_success)
        self.assertEqual(result["worker_exit_status"], 0)
        self.assertEqual(result["result"]["state"], "ready_for_review")
        self.assertFalse(result["result"]["accepted_by_gpt"])
        self.assertEqual(result["result"]["observations"]["actual_changed_files"], ["main.txt"])
        self.assertEqual(self.review(task)["state"], "accepted")

    def test_api_windows_backend_is_bound_and_cannot_switch(self):
        task = self.create(route="api", executor="example-api")
        settings = d.read_json(self.profile / "worker.json")
        settings["windows_sandbox"] = "mxc"
        d.write_json(self.profile / "worker.json", settings)
        with self.assertRaisesRegex(ValueError, "differ"):
            self.run_api(task, self.fake_success)
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    @unittest.skipUnless(os.name == "nt", "Windows backend is required only on Windows")
    def test_api_missing_backend_does_not_claim_task(self):
        settings = d.read_json(self.profile / "worker.json")
        settings.pop("windows_sandbox")
        d.write_json(self.profile / "worker.json", settings)
        task = self.create(route="api", executor="example-api")
        with self.assertRaises(ValueError):
            self.run_api(task, lambda *args: self.fail("Must not start a worker"))
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_api_exception_submits_failure_without_exposing_message(self):
        task = self.create(route="api", executor="example-api")
        def fail(root, args):
            raise ValueError("do-not-print-secret")
        result = self.run_api(task, fail)
        self.assertEqual(result["worker_exit_status"], 2)
        self.assertEqual(result["result"]["state"], "failed")
        self.assertNotIn("do-not-print-secret", json.dumps(result))
        with self.assertRaises(ValueError):
            self.create()

    def test_api_malformed_output_fails_instead_of_remaining_claimed(self):
        task = self.create(route="api", executor="example-api")
        def malformed(root, args):
            output = Path(args.output_dir)
            output.mkdir()
            (output / "result.json").write_text("[", encoding="utf-8")
            return 0
        result = self.run_api(task, malformed)
        self.assertEqual(result["result"]["state"], "failed")

    def test_api_zero_exit_with_no_report_is_failure(self):
        task = self.create(route="api", executor="example-api")
        result = self.run_api(task, lambda root, args: 0)
        self.assertEqual(result["worker_exit_status"], 2)
        self.assertEqual(result["result"]["state"], "failed")

    def test_api_timeout_is_failure_and_preserves_code(self):
        task = self.create(route="api", executor="example-api")
        result = self.run_api(task, lambda root, args: 124)
        self.assertEqual(result["worker_exit_status"], 124)
        self.assertEqual(result["result"]["state"], "failed")

    def test_api_failure_is_nonzero_at_cli_boundary(self):
        with patch.object(d, "run_api", return_value={"worker_exit_status": 124}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = d.main(["--board", str(self.board), "run-api", "--task-id", "task-offline",
                           "--api-root", str(self.profile)])
        self.assertEqual(code, 124)

    def test_desktop_cannot_be_run_as_api(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.run_api(task, self.fake_success)
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_cli_task_binds_a_registered_cli_executor(self):
        with self.assertRaises(ValueError):
            self.create(route="cli", executor="workbuddy")
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        self.assertEqual(task["next_step"], "run-cli")
        binding = d.read_json(Path(task["task_directory"]) / "task.json")["cli"]
        self.assertEqual(binding["executable"], str(self.fake_cli.resolve()))

    def test_cli_route_requires_a_clean_git_checkout(self):
        with self.assertRaises(ValueError):
            self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli), allow_no_git=True)

    def test_cli_missing_executable_is_rejected_at_creation(self):
        with self.assertRaises(ValueError):
            self.create(route="cli", executor="codebuddy", cli_path=str(self.root / "missing" / "codebuddy.exe"))

    def test_cli_dry_run_shows_argv_without_dispatching(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli), model="fake-model")
        result = self.run_cli(task, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIn("-p", result["argv"])
        self.assertIn("--model", result["argv"])
        self.assertFalse(self.argv_file.exists())
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_cli_dispatch_reaches_the_common_review_protocol(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        self.configure_fake(task)
        result = self.run_cli(task)
        self.assertEqual(result["worker_exit_status"], 0)
        self.assertEqual(result["result"]["state"], "ready_for_review")
        self.assertEqual(result["result"]["observations"]["actual_changed_files"], ["main.txt"])
        self.assertFalse(result["result"]["accepted_by_gpt"])
        received = json.loads(self.argv_file.read_text(encoding="utf-8"))
        self.assertEqual(received[0], "-p")
        self.assertIn("TASK.md", received[1])
        self.assertTrue((Path(task["task_directory"]) / "cli-run.json").is_file())
        self.assertEqual(self.review(task)["state"], "accepted")

    def test_cli_worker_without_report_is_recorded_as_failure(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        self.configure_fake(task, silent=True)
        result = self.run_cli(task)
        self.assertEqual(result["worker_exit_status"], 2)
        self.assertEqual(result["result"]["state"], "failed")
        with self.assertRaises(ValueError):
            self.review(task)

    def test_cli_timeout_terminates_and_records_failure(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        self.configure_fake(task, sleep=30)
        result = self.run_cli(task, timeout=1)
        self.assertEqual(result["worker_exit_status"], 124)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["result"]["state"], "failed")

    def test_cli_executable_cannot_switch_after_creation(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        with self.assertRaises(ValueError):
            self.run_cli(task, executable=sys.executable)
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_desktop_task_cannot_be_run_as_cli(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.run_cli(task)
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")

    def test_cli_route_rejects_api_only_arguments(self):
        with self.assertRaises(ValueError):
            self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli),
                        api_root=str(self.profile))

    def test_cli_failure_is_nonzero_at_cli_boundary(self):
        with patch.object(d, "run_cli", return_value={"worker_exit_status": 124}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = d.main(["--board", str(self.board), "run-cli", "--task-id", "task-offline"])
        self.assertEqual(code, 124)

    def test_status_is_read_only_and_follows_the_lifecycle(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        queued = d.status(self.board, task["task_id"])
        self.assertEqual(queued["state"], "queued")
        self.assertFalse(queued["claimed"])
        self.assertIsNone(queued["worker_report"])
        self.assertEqual(d.task_state(Path(task["task_directory"]))[1]["state"], "queued")
        self.configure_fake(task)
        self.run_cli(task)
        done = d.status(self.board, task["task_id"])
        self.assertEqual(done["state"], "ready_for_review")
        self.assertTrue(done["claimed"])
        self.assertEqual(done["cli_run"]["exit_code"], 0)
        self.assertEqual(done["worker_report"]["status"], "completed")

    def test_status_shows_failure_details_after_an_empty_run(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli))
        self.configure_fake(task, silent=True)
        self.run_cli(task)
        snapshot = d.status(self.board, task["task_id"])
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["worker_report"]["status"], "failed")
        self.assertTrue(snapshot["worker_report"]["issues"])

    def test_status_tail_is_bounded_at_the_cli_boundary(self):
        task = self.create()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(d.main(["--board", str(self.board), "status", "--task-id", task["task_id"], "--tail", "999"]), 2)

    def test_plain_status_reads_as_a_short_report(self):
        task = self.create(route="cli", executor="codebuddy", cli_path=str(self.fake_cli), model="fake-model")
        self.configure_fake(task)
        self.run_cli(task)
        text = d.brief(d.status(self.board, task["task_id"]))
        self.assertIn("完成了，等你验收", text)
        self.assertIn("fake-model", text)
        self.assertIn("main.txt", text)
        self.assertIn("耗时", text)
        self.assertNotIn("{", text)

    def test_plain_status_is_available_at_the_cli_boundary(self):
        task, report = self.ready()
        d.submit(self.board, task["task_id"], report)
        d.collect(self.board, task["task_id"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = d.main(["--board", str(self.board), "status", "--task-id", task["task_id"], "--plain"])
        self.assertEqual(code, 0)
        self.assertIn("任务", output.getvalue())
        self.assertNotIn('"task_id"', output.getvalue())

    def test_windows_handoff_shell_quoting_preserves_literals(self):
        if os.name != "nt":
            self.skipTest("PowerShell argument roundtrip is Windows-specific")
        values = [r"C:\a path\b", "an' apostrophe", "$([int]9)", "`literal"]
        command = d.command_line([sys.executable, "-c", "import sys,json; print(json.dumps(sys.argv[1:]))", *values])
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                              check=True, capture_output=True, timeout=20)
        self.assertEqual(json.loads(proc.stdout), values)


if __name__ == "__main__":
    unittest.main()
