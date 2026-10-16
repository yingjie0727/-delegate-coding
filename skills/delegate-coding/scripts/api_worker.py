#!/usr/bin/env python3
"""Bounded Responses API coding worker. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
from urllib.parse import urlsplit
import uuid

SCHEMA_VERSION = 1
SECRET_PATTERN = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)
DEFAULT_KEY_ENV = "DEEPSEEK_API_KEY"
ACTIVE_ENV = "CODING_API_WORKER_ACTIVE"
WORKER_INSTRUCTIONS = """You implement a bounded coding task planned by a supervising GPT agent.
Read the supplied task and relevant repository instructions. Follow the specified
architecture, interfaces, constraints, allowed paths, and acceptance criteria.
Use local tools to implement and verify the task. Report blockers rather than
changing architecture. Do not delegate, spawn agents, commit, push, merge, change
Git configuration, or read credentials. Do not modify files outside the task scope.
If a required command is blocked, report it; do not bypass sandbox restrictions.
Return a concise report: changes, verification commands and actual outcomes,
remaining issues, and decisions required from the supervising GPT agent.
"""


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_root():
    return Path(os.environ.get("CODING_API_WORKER_ROOT") or
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) /
                    "CodexApiWorker")).expanduser().resolve()


def validate_settings(settings):
    fields = {"schema_version", "base_url", "model", "context_window", "key_env", "provider_name"}
    if not isinstance(settings, dict) or set(settings) - fields:
        raise ValueError("Invalid worker settings; use supported non-secret fields only")
    if type(settings.get("schema_version")) is not int or settings.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported settings schema; inspect worker.json")
    if not isinstance(settings.get("base_url"), str):
        raise ValueError("base_url must be an HTTPS API root without credentials/query/fragment")
    try:
        url = urlsplit(settings["base_url"])
        invalid_url = (url.scheme != "https" or not url.hostname or url.username or url.password or
                       url.query or url.fragment or url.path.rstrip("/").endswith(("/responses", "/chat/completions")))
    except (TypeError, ValueError):
        raise ValueError("base_url must be an HTTPS API root without credentials/query/fragment") from None
    if invalid_url:
        raise ValueError("base_url must be an HTTPS API root without credentials/query/fragment")
    if not isinstance(settings.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9._:/-]+", settings["model"]):
        raise ValueError("Invalid model ID")
    window = settings.get("context_window")
    if type(window) is not int or window < 8192:
        raise ValueError("context_window must be an integer >= 8192, within the provider's real limit")
    key_env = settings.get("key_env", DEFAULT_KEY_ENV)
    if (not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env) or
            key_env.upper() in {"PATH", "PATHEXT", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", ACTIVE_ENV} or
            key_env.upper().startswith(("CODEX_", "CHATGPT_", "CODING_API_WORKER_"))):
        raise ValueError("key_env must be a valid, non-reserved environment variable name")
    provider_name = settings.get("provider_name", "API implementation worker")
    if not isinstance(provider_name, str) or not provider_name.strip() or len(provider_name) > 120 or any(ord(c) < 32 for c in provider_name):
        raise ValueError("provider_name must be a nonempty display name without control characters")
    return {**settings, "key_env": key_env, "provider_name": provider_name}


def init(root, args):
    target = root / "worker.json"
    settings = validate_settings({"schema_version": SCHEMA_VERSION, "base_url": args.base_url.rstrip("/"),
                                  "model": args.model, "context_window": args.context_window,
                                  "key_env": getattr(args, "key_env", DEFAULT_KEY_ENV),
                                  "provider_name": getattr(args, "provider_name", "API implementation worker")})
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves existing choices on repeated invocation.
    with target.open("x", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Initialized: {target}\nNo API request was made. Next: key-set, doctor.")
    return 0


def dpapi(data: bytes, decrypt=False):
    if os.name != "nt":
        raise ValueError("Stored keys are Windows-only; use the profile's key_env on this platform")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buf), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "API coding worker", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI failed for the current user")
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def get_key(root, settings=None):
    settings = settings or validate_settings(load(root / "worker.json"))
    env_key = os.environ.get(settings["key_env"], "").strip()
    if env_key:
        return env_key
    key_file = root / "key.dpapi"
    return dpapi(key_file.read_bytes(), decrypt=True).decode("utf-8") if key_file.exists() else ""


def key_set(root):
    settings = validate_settings(load(root / "worker.json"))
    if os.name != "nt":
        raise ValueError("Use " + settings["key_env"] + " on non-Windows platforms")
    if not sys.stdin.isatty():
        raise ValueError("Run key-set in a real local terminal; never pass the key in chat/arguments")
    key = getpass.getpass(settings["provider_name"] + " API key (hidden): ").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Expected a nonempty key without whitespace")
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f"key.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(dpapi(key.encode("utf-8")))
        os.replace(temp, root / "key.dpapi")
    finally:
        temp.unlink(missing_ok=True)
    print("Stored current-user DPAPI credential. The key was not printed.")
    return 0


def resolve_codex(explicit=None):
    candidate = explicit or shutil.which("codex") or shutil.which("codex.exe")
    if not candidate:
        raise ValueError("Codex CLI not on PATH; install the official CLI or supply --codex")
    path = Path(candidate).expanduser().resolve()
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # npm's adjacent JS entrypoint avoids cmd.exe shell interpolation.
        path = path.parent / "node_modules/@openai/codex/bin/codex.js"
    if not path.is_file():
        raise ValueError("Codex executable/official npm JS entrypoint not found")
    if path.suffix.lower() in (".js", ".mjs"):
        node = shutil.which("node")
        if not node:
            raise ValueError("Node is required for the Codex JS entrypoint")
        return [node, str(path)]
    if os.name == "nt" and path.suffix.lower() != ".exe":
        raise ValueError("Supply codex.exe or the official codex.js entrypoint")
    return [str(path)]


def child_env(home, key, key_env=DEFAULT_KEY_ENV):
    env = {k: v for k, v in os.environ.items()
           if not SECRET_PATTERN.search(k) and not k.upper().startswith(("CODEX_", "OPENAI_", "CHATGPT_"))}
    env.update(CODEX_HOME=str(home), **{key_env: key, ACTIVE_ENV: "1"})
    return env


def git(workspace, *args, check=True):
    return subprocess.run(["git", "-C", str(workspace), "-c", "core.quotePath=false", *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
                          timeout=30).stdout


def validate_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Paths must be nonempty repository-relative paths using /")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError("Absolute paths and parent traversal are not allowed in task paths")
    return value


def read_task(path):
    task = load(path)
    for name in ("title", "goal"):
        if not isinstance(task.get(name), str) or not task[name].strip():
            raise ValueError(f"Task requires nonempty {name}")
    for name in ("allowed_paths", "acceptance", "constraints", "context_files"):
        value = task.get(name, [] if name == "context_files" else None)
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise ValueError(f"Task {name} must be an array of nonempty strings")
        if name in ("allowed_paths", "acceptance") and not value:
            raise ValueError(f"Task {name} cannot be empty")
    for value in task["allowed_paths"] + task.get("context_files", []):
        validate_relative(value)
    return task


def allowed(path, scopes):
    return any(scope == "." or path == scope or
               (scope.endswith("/") and path.startswith(scope)) for scope in scopes)


def prepare_home(home, settings):
    settings = validate_settings(settings)
    home.mkdir(parents=True, exist_ok=True)
    model, window = settings["model"], settings["context_window"]
    instructions = home / "worker-instructions.md"
    instructions.write_text(WORKER_INSTRUCTIONS, encoding="utf-8")
    catalog = {"models": [{
        "slug": model, "display_name": settings["provider_name"], "description": "Bounded text coding worker",
        "default_reasoning_level": "high", "supported_reasoning_levels": [{"effort": "high", "description": "Implementation"}],
        "shell_type": "unified_exec", "visibility": "list", "supported_in_api": True, "priority": 0,
        "upgrade": None, "base_instructions": WORKER_INSTRUCTIONS, "model_messages": None,
        "supports_reasoning_summaries": False, "support_verbosity": False, "default_verbosity": "low",
        "apply_patch_tool_type": "freeform", "web_search_tool_type": "text",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "context_window": window, "effective_context_window_percent": 95,
        "input_modalities": ["text"], "prefer_websockets": False,
        "experimental_supported_tools": []
    }]}
    dump(home / "models.json", catalog)
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    config = f'''model = {q(model)}
model_provider = "api_worker"
model_catalog_json = {q(home / 'models.json')}
model_instructions_file = {q(instructions)}
model_context_window = {window}
model_auto_compact_token_limit = {min(80000, int(window * 0.6))}
model_reasoning_effort = "high"
model_reasoning_summary = "none"
web_search = "disabled"
approval_policy = "never"
sandbox_mode = "workspace-write"

[model_providers.api_worker]
name = {q(settings['provider_name'])}
base_url = {q(settings['base_url'])}
wire_api = "responses"
env_key = {q(settings['key_env'])}
requires_openai_auth = false
supports_websockets = false
request_max_retries = 1
stream_max_retries = 1

[model_providers.api_worker.capabilities]
remote_compaction = "unsupported"
external_web_access = false

[agents]
enabled = false

[features]
hooks = false

[sandbox_workspace_write]
network_access = false

[shell_environment_policy]
inherit = "all"
ignore_default_excludes = false
filters = {{ "*KEY*" = "exclude", "*TOKEN*" = "exclude", "*SECRET*" = "exclude", "*PASSWORD*" = "exclude", "*CREDENTIAL*" = "exclude", {q(settings['key_env'])} = "exclude" }}
'''
    tomllib.loads(config)
    (home / "config.toml").write_text(config, encoding="utf-8")


def doctor(root, codex):
    report = {"offline": True, "worker_root": str(root), "checks": {}}
    checks = report["checks"]
    settings = None
    try:
        settings = validate_settings(load(root / "worker.json"))
        checks["settings"] = {"ok": True, **settings}
    except (OSError, ValueError, KeyError):
        checks["settings"] = {"ok": False, "action": "Initialize or inspect worker.json"}
    checks["git"] = {"ok": bool(shutil.which("git"))}
    try:
        checks["credential"] = {"ok": bool(get_key(root, settings))} if checks["settings"]["ok"] else {"ok": False}
    except (OSError, ValueError):
        checks["credential"] = {"ok": False, "action": "Reset the stored key for this Windows user"}
    try:
        command = resolve_codex(codex)
        env = child_env(root / "diagnostic-home", "", settings["key_env"] if settings else DEFAULT_KEY_ENV)
        # --version and --help are offline and do not create a model turn.
        version = subprocess.run(command + ["--version"], env=env, capture_output=True, timeout=15, check=True)
        help_result = subprocess.run(command + ["exec", "--help"], env=env, capture_output=True, timeout=15, check=True)
        help_text = help_result.stdout.decode("utf-8", errors="replace")
        checks["codex"] = {"ok": all(x in help_text for x in ("--json", "--output-last-message", "--sandbox")),
                           "command": command, "version": version.stdout.decode("utf-8", errors="replace").strip()}
        if checks["settings"]["ok"]:
            diagnostic_home = root / "diagnostic-home"
            prepare_home(diagnostic_home, settings)
            catalog_result = subprocess.run(command + ["debug", "models"],
                env=child_env(diagnostic_home, "", settings["key_env"]), capture_output=True, timeout=20, check=True)
            catalog = json.loads(catalog_result.stdout)
            models = catalog.get("models", []) if isinstance(catalog, dict) else catalog
            checks["catalog"] = {"ok": any(m.get("slug") == settings["model"] for m in models)}
    except (OSError, ValueError, subprocess.SubprocessError):
        checks.setdefault("codex", {"ok": False, "action": "Install/update Codex CLI or supply --codex"})
        checks["catalog"] = {"ok": False, "action": "Inspect settings and CLI metadata compatibility"}
    report["ready_for_live_test"] = all(v["ok"] for v in checks.values())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_live_test"] else 2


class WindowsJob:
    """Kill associated worker processes if this handle closes, including on runner exit."""
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Cannot create worker Windows Job Object")

    def assign(self, proc):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(proc._handle)):
            proc.kill()
            proc.wait()
            raise OSError("Cannot assign worker to Windows Job Object; worker stopped")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def redact_stream(pipe, path, secret, events=None):
    pending = b""
    secret = secret.encode("utf-8")
    written, cap = 0, 8 * 1024 * 1024
    with path.open("wb") as output:
        while True:
            line = pipe.readline(1024 * 1024)
            if not line:
                break
            if events is not None:
                with contextlib.suppress(ValueError, UnicodeError):
                    item = json.loads(line)
                    if isinstance(item, dict):
                        kind = item.get("type")
                        events["completed"] |= kind == "turn.completed"
                        events["failed"] |= kind in ("turn.failed", "error")
            pending += line
            cutoff = max(0, len(pending) - max(0, len(secret) - 1))
            if secret:
                at = pending.find(secret)
                while at >= 0 and at < cutoff:
                    cutoff = max(cutoff, at + len(secret))
                    at = pending.find(secret, at + len(secret))
            chunk, pending = pending[:cutoff], pending[cutoff:]
            if secret:
                chunk = chunk.replace(secret, b"[REDACTED]")
            if written < cap:
                output.write(chunk[:cap - written])
                written += len(chunk)
        pending = pending.replace(secret, b"[REDACTED]") if secret else pending
        if written < cap:
            output.write(pending[:cap - written])
        if written + len(pending) > cap:
            output.write(b"\n[LOG TRUNCATED]\n")
    pipe.close()


def execute(command, prompt, home, key, output_dir, timeout, key_env=DEFAULT_KEY_ENV):
    job, proc = WindowsJob(), None
    events = {"completed": False, "failed": False}
    readers, writer, status, code = [], None, "failed", 1
    try:
        proc = subprocess.Popen(command, cwd=str(output_dir),
                                env=child_env(home, key, key_env), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=os.name != "nt",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        job.assign(proc)
        for pipe, name, collector in ((proc.stdout, "events.jsonl", events), (proc.stderr, "stderr.log", None)):
            thread = threading.Thread(target=redact_stream, args=(pipe, output_dir / name, key, collector), daemon=True)
            thread.start()
            readers.append(thread)
        # CLI bootstrap may stall before reading stdin. Keep timeout active while
        # feeding even a prompt larger than the OS pipe buffer.
        def feed():
            try:
                proc.stdin.write(prompt.encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    proc.stdin.close()
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        deadline, next_update = time.monotonic() + timeout, time.monotonic() + 20
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                status, code = "timed_out", 124
                break
            if time.monotonic() >= next_update:
                print("API worker is running; artifacts: " + str(output_dir), flush=True)
                next_update = time.monotonic() + 20
            time.sleep(0.2)
        else:
            code = proc.returncode
            status = "completed" if code == 0 else "failed"
    except KeyboardInterrupt:
        status, code = "interrupted", 130
    finally:
        job.close()
        if proc is not None:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            elif proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            if writer:
                writer.join(timeout=10)
            elif proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        for thread in readers:
            thread.join(timeout=10)
        if any(thread.is_alive() for thread in readers) or (writer and writer.is_alive()):
            raise OSError("Worker log reader did not close; inspect worker processes")
    return status, code, events


def run(root, args):
    if os.environ.get(ACTIVE_ENV) == "1":
        raise ValueError("Recursive worker invocation is disabled")
    settings = validate_settings(load(root / "worker.json"))
    task = read_task(Path(args.task))
    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    root = Path(root).expanduser().resolve()
    if root == workspace or workspace in root.parents:
        raise ValueError("API profile must be outside the implementation workspace")
    repo_root = Path(os.fsdecode(git(workspace, "rev-parse", "--show-toplevel")).strip()).resolve()
    if workspace != repo_root:
        raise ValueError("--workspace must be the repository/worktree root")
    if git(workspace, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Working tree is not clean; preserve changes and prepare a clean checkout")
    base = git(workspace, "rev-parse", "HEAD").decode().strip()
    command = resolve_codex(args.codex)
    key = get_key(root, settings) if not args.dry_run else ""
    if not args.dry_run and not key:
        raise ValueError("No API credential; run key-set locally or supply " + settings["key_env"])
    if key and any(c.isspace() for c in key):
        raise ValueError("Invalid credential format")
    output_dir = (Path(args.output_dir).expanduser() if args.output_dir else
                  root / "runs" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])).resolve()
    if output_dir == workspace or workspace in output_dir.parents:
        raise ValueError("Run output must be outside the implementation checkout")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Output directory must be new or empty")
    locks = root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / (hashlib.sha256(os.path.normcase(str(workspace)).encode()).hexdigest() + ".lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("A worker lock exists; inspect active/stale workers before removing it") from None
    try:
        with os.fdopen(lock_fd, "w") as f:
            f.write(str(os.getpid()))
        output_dir.mkdir(parents=True, exist_ok=True)
        home = output_dir / "codex-home"
        prepare_home(home, settings)
        dump(output_dir / "task.json", task)
        prompt = (WORKER_INSTRUCTIONS + "\nTask specification:\n" + json.dumps(task, ensure_ascii=False) +
                  "\nDo not modify the supplied task or runner artifacts. Report actual verification outcomes.")
        if len(prompt.encode("utf-8")) > 128 * 1024:
            raise ValueError("Task specification exceeds 128 KiB")
        argv = command + ["exec", "--cd", str(workspace), "--sandbox", "workspace-write", "--json",
                          "-c", "agents.enabled=false", "-c", "features.hooks=false",
                          "-c", 'approval_policy="never"', "-c", "sandbox_workspace_write.network_access=false",
                          "--output-last-message", str(output_dir / "final.md"), "-"]
        dump(output_dir / "invocation.json", {"argv": argv, "workspace": str(workspace),
             "base_commit": base, "model": settings["model"], "base_url": settings["base_url"], "dry_run": args.dry_run})
        print("Worker artifacts: " + str(output_dir), flush=True)
        if args.dry_run:
            dump(output_dir / "result.json", {"status": "dry_run", "api_called": False})
            return 0
        status, code, events = execute(argv, prompt, home, key, output_dir, args.timeout, settings["key_env"])
        final = output_dir / "final.md"
        if final.is_file():
            final.write_text(final.read_text(encoding="utf-8", errors="replace").replace(key, "[REDACTED]"), encoding="utf-8")
        end_head = git(workspace, "rev-parse", "HEAD").decode().strip()
        changed = git(workspace, "diff", "--name-only", "--no-renames", "-z", base, "--")
        untracked = git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
        staged = git(workspace, "diff", "--cached", "--name-only", "-z")
        names = sorted(set(os.fsdecode(v) for v in (changed + untracked).split(b"\0") if v))
        violations = [v for v in names if not allowed(v, task["allowed_paths"])]
        (output_dir / "changes.patch").write_bytes(git(workspace, "diff", "--binary", "--no-ext-diff", "--no-renames", base, "--"))
        (output_dir / "git-status.txt").write_bytes(git(workspace, "status", "--porcelain", "--untracked-files=all"))
        if status == "completed":
            status = "ready_for_review" if (events["completed"] and not events["failed"] and
                     final.is_file() and final.stat().st_size) else "incomplete"
            code = 0 if status == "ready_for_review" else 1
        if violations or end_head != base or staged:
            status = "review_required"
            if code not in (124, 130):
                code = 3
        result = {"status": status, "worker_exit_code": code, "events": events,
                  "workspace": str(workspace), "base_commit": base, "end_commit": end_head,
                  "changed_files": names, "scope_violations": violations,
                  "staged_files": [os.fsdecode(v) for v in staged.split(b"\0") if v],
                  "untracked_files": [os.fsdecode(v) for v in untracked.split(b"\0") if v],
                  "accepted_by_gpt": False, "output_directory": str(output_dir)}
        dump(output_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return code
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-root", type=Path, default=default_root())
    parser.add_argument("--codex", help="Absolute codex.exe or official codex.js entrypoint")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init", help="Create non-secret worker settings, offline")
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--context-window", type=int, default=128000)
    p.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="Credential environment variable for this profile")
    p.add_argument("--provider-name", default="API implementation worker", help="Non-secret display name")
    sub.add_parser("doctor", help="Offline prerequisites check; no model request")
    sub.add_parser("key-set", help="Hidden local input; Windows current-user DPAPI")
    sub.add_parser("key-delete", help="Delete stored credential only")
    p = sub.add_parser("run", help="Run one task; sends data to provider and can incur charges")
    p.add_argument("--workspace", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--output-dir")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--dry-run", action="store_true", help="Prepare command/config without credentials or model invocation")
    args = parser.parse_args(argv)
    root = args.worker_root.expanduser().resolve()
    try:
        if args.action == "init":
            return init(root, args)
        if args.action == "doctor":
            return doctor(root, args.codex)
        if args.action == "key-set":
            return key_set(root)
        if args.action == "key-delete":
            (root / "key.dpapi").unlink(missing_ok=True)
            print("Stored key removed; environment credentials/provider keys are unchanged.")
            return 0
        if args.timeout <= 0:
            raise ValueError("Timeout must be positive")
        return run(root, args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # Do not dump tracebacks, credential values, or subprocess output.
        detail = str(exc) if isinstance(exc, (ValueError, OSError)) else "Inspect prerequisites and run artifacts; a CLI/Git command failed."
        print(f"Worker error: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
