"""Offline tests: no actual Codex process, API, credentials, or sandbox setup."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import api_worker
import check_executor as checker


SETTINGS = {"schema_version": 1, "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
            "context_window": 128000, "key_env": "DEEPSEEK_API_KEY", "windows_sandbox": "unelevated"}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.nonce = "a" * 32
        self.outside = self.base / (".executor-outside-" + self.nonce + ".txt")
        self.inside = self.workspace / (".executor-probe-" + self.nonce + ".txt")
        self.probe = types.ModuleType("offline_probe")
        exec(compile(checker.PROBE_SOURCE, "offline_probe", "exec"), self.probe.__dict__)

    def run_probe(self, mode):
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.probe.main([mode, str(self.workspace), str(self.outside), self.nonce])
        return code, output.getvalue()

    def test_inside_writes_reads_and_removes_own_file(self):
        code, output = self.run_probe("inside")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "EXECUTOR_INSIDE_OK_" + self.nonce)
        self.assertFalse(self.inside.exists())

    def test_inside_collision_preserves_existing_file(self):
        self.inside.write_text("preserve", encoding="utf-8")
        code, output = self.run_probe("inside")
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertEqual(self.inside.read_text(encoding="utf-8"), "preserve")

    def test_outside_permission_denied_is_expected(self):
        actual_open = Path.open

        def denied(target, *args, **kwargs):
            if target == self.outside:
                raise PermissionError("simulated ACL")
            return actual_open(target, *args, **kwargs)

        with patch.object(Path, "open", denied):
            code, output = self.run_probe("outside")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "EXECUTOR_OUTSIDE_DENIED_" + self.nonce)
        self.assertFalse(self.outside.exists())

    def test_outside_creation_is_failure_and_is_removed(self):
        code, output = self.run_probe("outside")
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertFalse(self.outside.exists())

    def test_write_denied_after_creation_is_failure_with_cleanup(self):
        actual_open = Path.open

        class DeniedWriter:
            def __init__(self, stream):
                self.stream = stream

            def write(self, value):
                raise PermissionError("write after create was blocked")

            def close(self):
                self.stream.close()

        def denied_write(target, *args, **kwargs):
            stream = actual_open(target, *args, **kwargs)
            return DeniedWriter(stream) if target == self.outside else stream

        with patch.object(Path, "open", denied_write):
            code, output = self.run_probe("outside")
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertFalse(self.outside.exists())

    def test_inside_read_failure_still_cleans_up(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("read blocked")):
            code, output = self.run_probe("inside")
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertFalse(self.inside.exists())

    def test_outside_collision_is_failure_and_preserves_existing_file(self):
        self.outside.write_text("keep", encoding="utf-8")
        code, output = self.run_probe("outside")
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertEqual(self.outside.read_text(encoding="utf-8"), "keep")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / "project"
        self.workspace.mkdir()
        self.root = self.base / "profile"
        self.output = self.base / "probe-output"
        self.credential = patch.object(api_worker, "get_key", side_effect=AssertionError("credential read"))
        self.credential.start()
        self.addCleanup(self.credential.stop)
        self.home = patch.object(api_worker, "prepare_home")
        self.prepared = self.home.start()
        self.addCleanup(self.home.stop)
        self.calls = []

    def execute_ok(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        mode, _workspace, _outside, nonce = argv[-4:]
        marker = ("EXECUTOR_INSIDE_OK_" if mode == "inside" else "EXECUTOR_OUTSIDE_DENIED_") + nonce
        (kwargs["output_dir"] / "events.jsonl").write_text(marker + "\n", encoding="utf-8")
        return "completed", 0, {"completed": False, "failed": False}

    def check(self, execute=None, settings=None, platform="nt", root=None, output=None):
        with patch.object(checker, "_is_windows", return_value=platform == "nt"), patch.object(api_worker, "execute", execute or self.execute_ok):
            return checker.check(root or self.root, SETTINGS if settings is None else settings,
                                 ["offline-codex.exe"], self.workspace, output or self.output)

    def test_two_stage_success_and_private_home_and_no_credentials(self):
        result = self.check()
        self.assertTrue(result["passed"])
        self.assertFalse(result["credential_read"])
        self.assertFalse(result["api_called"])
        self.assertEqual(len(self.calls), 2)
        self.prepared.assert_called_once()
        for argv, kwargs in self.calls:
            self.assertEqual(kwargs["home"], self.root / "codex-home")
            self.assertEqual(kwargs["key"], "")
            self.assertEqual(kwargs["prompt"], "")
            self.assertEqual(kwargs["key_env"], "DEEPSEEK_API_KEY")
            sandbox_argv = argv[1:argv.index("--")]
            self.assertEqual(sandbox_argv, ["sandbox", "--include-managed-config", "--permission-profile",
                                            ":workspace", "--cd", str(self.workspace)])
        saved = json.loads((self.output / "check-result.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["passed"])

    def test_inside_failure_stops_before_outside(self):
        def fail(argv, **kwargs):
            self.calls.append((argv, kwargs))
            (kwargs["output_dir"] / "events.jsonl").write_text("", encoding="utf-8")
            return "failed", 7, {}

        result = self.check(fail)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "inside_failed")
        self.assertEqual(len(self.calls), 1)

    def test_missing_marker_cannot_pass_on_exit_code_alone(self):
        def missing(argv, **kwargs):
            (kwargs["output_dir"] / "events.jsonl").write_text("other output\n", encoding="utf-8")
            return "completed", 0, {"completed": True}

        self.assertEqual(self.check(missing)["status"], "inside_failed")

    def test_oversized_stdout_cannot_pass(self):
        def oversized(argv, **kwargs):
            self.execute_ok(argv, **kwargs)
            with (kwargs["output_dir"] / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write("x" * checker.MAX_STDOUT)
            return "completed", 0, {}

        self.assertEqual(self.check(oversized)["status"], "inside_failed")

    def test_outside_missing_denial_is_failure(self):
        def outside_allowed(argv, **kwargs):
            if argv[-4] == "inside":
                return self.execute_ok(argv, **kwargs)
            (kwargs["output_dir"] / "events.jsonl").write_text("", encoding="utf-8")
            return "failed", 8, {}

        result = self.check(outside_allowed)
        self.assertEqual(result["status"], "outside_failed")

    def test_missing_backend_fails_without_execute_or_home(self):
        settings = {key: value for key, value in SETTINGS.items() if key != "windows_sandbox"}
        result = self.check(settings=settings)
        self.assertFalse(result["passed"])
        self.assertEqual(self.calls, [])
        self.prepared.assert_not_called()

    def test_non_windows_is_controlled_unsupported(self):
        result = self.check(platform="posix")
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(self.calls, [])
        self.prepared.assert_not_called()

    def test_profile_inside_workspace_is_rejected(self):
        result = self.check(root=self.workspace / "profile")
        self.assertEqual(result["status"], "invalid_paths")
        self.prepared.assert_not_called()

    def test_output_inside_workspace_is_rejected_without_write(self):
        output = self.workspace / "probe"
        result = self.check(output=output)
        self.assertEqual(result["status"], "invalid_paths")
        self.assertFalse(output.exists())

    def test_nonempty_output_is_preserved(self):
        self.output.mkdir()
        marker = self.output / "existing.txt"
        marker.write_text("keep", encoding="utf-8")
        result = self.check()
        self.assertEqual(result["status"], "output_not_empty")
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_exception_message_is_not_saved(self):
        def fail(*args, **kwargs):
            raise OSError("never serialize this secret-looking text")

        result = self.check(fail)
        self.assertEqual(result["status"], "check_failed")
        self.assertNotIn("secret-looking", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
