#!/usr/bin/env python3
"""Check a Windows worker sandbox without reading a key or calling a model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

import api_worker


MAX_STDOUT = 64 * 1024
PROBE_SOURCE = '''from pathlib import Path
import re
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        return 2
    mode, workspace_text, outside_text, nonce = argv
    if mode not in ("inside", "outside") or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        return 2
    workspace = Path(workspace_text).resolve()
    outside = Path(outside_text).resolve()
    if not workspace.is_dir() or outside == workspace or workspace in outside.parents:
        return 2
    if outside.name != ".executor-outside-" + nonce + ".txt":
        return 2
    if mode == "inside":
        target = workspace / (".executor-probe-" + nonce + ".txt")
        created = False
        try:
            with target.open("x", encoding="utf-8") as output:
                created = True
                output.write(nonce)
            if target.read_text(encoding="utf-8") != nonce:
                return 3
        except OSError:
            return 4
        finally:
            if created:
                try:
                    target.unlink()
                except OSError:
                    return 5
        print("EXECUTOR_INSIDE_OK_" + nonce)
        return 0
    try:
        output = outside.open("x", encoding="utf-8")
    except PermissionError:
        print("EXECUTOR_OUTSIDE_DENIED_" + nonce)
        return 0
    except OSError:
        return 6
    try:
        output.write(nonce)
    except OSError:
        pass
    finally:
        try:
            output.close()
        finally:
            try:
                outside.unlink()
            except OSError:
                return 7
    # Creation succeeded: the expected workspace boundary was not enforced.
    return 8


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        raise SystemExit(9)
'''


def _inside(path: Path, workspace: Path) -> bool:
    return path == workspace or workspace in path.parents


def _is_windows() -> bool:
    return os.name == "nt"


def _marker_present(path: Path, marker: str) -> bool:
    """Read a small ordinary stdout log; this command emits no model events."""
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_STDOUT + 1)
        if len(content) > MAX_STDOUT:
            return False
        return content.decode("utf-8", errors="replace").splitlines().count(marker) == 1
    except OSError:
        return False


def check(root, settings, command, workspace, output_dir, timeout=25) -> dict:
    """Use the live worker's private home and sandbox; never weaken its policy."""
    report = {"schema_version": 1, "passed": False, "credential_read": False,
              "api_called": False, "status": "not_started", "checks": {}}
    safe_output = None

    def finish(status):
        report["status"] = status
        if safe_output is not None:
            try:
                api_worker.dump(safe_output / "check-result.json", report)
            except OSError:
                report["passed"] = False
                report["status"] = "result_write_failed"
        return report

    try:
        workspace = Path(workspace).expanduser().resolve(strict=True)
        root = Path(root).expanduser().resolve()
        output_dir = Path(output_dir).expanduser().resolve()
        if not workspace.is_dir() or _inside(root, workspace) or _inside(output_dir, workspace):
            return finish("invalid_paths")
        home = root / "codex-home"
        # Probe artifacts must not share the profile's persistent sandbox state.
        if _inside(output_dir, home) or _inside(home, output_dir):
            return finish("invalid_paths")
        if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
            return finish("output_not_empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_output = output_dir
        report.update(workspace=str(workspace), worker_root=str(root),
                      codex_home=str(home), output_directory=str(output_dir))
        if not _is_windows():
            return finish("unsupported_platform")
        settings = api_worker.validate_settings(settings)
        api_worker.require_windows_sandbox(settings)
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            return finish("invalid_configuration")
        if isinstance(command, (str, Path)):
            command = api_worker.resolve_codex(command)
        if (not isinstance(command, (list, tuple)) or not command or
                any(not isinstance(value, str) or not value or "\0" in value for value in command)):
            return finish("invalid_configuration")
        report["windows_sandbox"] = settings["windows_sandbox"]
        api_worker.prepare_home(home, settings)
        nonce = uuid.uuid4().hex
        probe = output_dir / "executor_probe.py"
        probe.write_text(PROBE_SOURCE, encoding="utf-8")
        inside_target = workspace / (".executor-probe-" + nonce + ".txt")
        outside_target = output_dir / (".executor-outside-" + nonce + ".txt")
        if inside_target.exists() or outside_target.exists():
            return finish("probe_path_conflict")
        for mode in ("inside", "outside"):
            case_dir = output_dir / mode
            case_dir.mkdir()
            argv = list(command) + ["sandbox", "--include-managed-config", "--permission-profile", ":workspace",
                                   "--cd", str(workspace), "--", sys.executable, "-X", "utf8", "-B",
                                   str(probe), mode, str(workspace), str(outside_target), nonce]
            status, code, _events = api_worker.execute(
                argv, prompt="", home=home, key="", output_dir=case_dir,
                timeout=timeout, key_env=settings["key_env"])
            marker = ("EXECUTOR_INSIDE_OK_" if mode == "inside" else "EXECUTOR_OUTSIDE_DENIED_") + nonce
            marker_ok = _marker_present(case_dir / "events.jsonl", marker)
            cleaned = not (inside_target if mode == "inside" else outside_target).exists()
            report["checks"][mode] = {"passed": status == "completed" and code == 0 and marker_ok and cleaned,
                                      "status": status if status in {"completed", "failed", "timed_out", "interrupted"} else "failed",
                                      "exit_code": code if type(code) is int else None,
                                      "marker_received": marker_ok, "temporary_file_removed": cleaned}
            if not report["checks"][mode]["passed"]:
                return finish(mode + "_failed")
        report["passed"] = True
        return finish("passed")
    except Exception:
        # Never serialize exception text, process output, or inherited credentials.
        return finish("check_failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args(argv)
    try:
        # worker.json contains public provider settings; no credential source is read.
        settings = api_worker.load(args.worker_root / "worker.json")
    except Exception:
        print(json.dumps({"passed": False, "credential_read": False, "api_called": False,
                          "status": "invalid_configuration"}, ensure_ascii=False))
        return 2
    result = check(args.worker_root, settings, args.codex, args.workspace, args.output_dir, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
