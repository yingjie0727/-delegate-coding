#!/usr/bin/env python3
"""Audited desktop delivery bookkeeping; this program never operates the UI.

Prepare and begin before the selected desktop controller sends HANDOFF.txt. Record only
what was observed afterwards. Execution, report validation and acceptance stay
in delegate.py. A missing acknowledgement is never permission to resend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

import delegate as d


VERSION = 1
DELIVERY_FILE = "DELIVERY.json"
BOUND_FILES = ("task.json", "spec.json", "TASK.md", "controller.py", "HANDOFF.txt")
IN_FLIGHT = {"sending", "uncertain"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must contain actual observed information")
    if len(value) > 16000:
        raise ValueError(f"{label} is too long")
    return value.strip()


def _context(board, task_id):
    d.require_board(board)
    job = d.job_dir(board, task_id)
    task, state = d.task_state(job)
    if task.get("route") != "desktop":
        raise ValueError("Desktop delivery accepts desktop tasks only; do not switch routes")
    return job, task, state


def _hashes(job):
    result = {}
    for name in BOUND_FILES:
        path = job / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Bound task file is missing or a symbolic link: {name}")
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError(f"Bound task file is too large: {name}")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _load(job, task):
    path = job / DELIVERY_FILE
    if not path.exists():
        return None
    value = d.read_json(path)
    if (not isinstance(value, dict) or value.get("schema_version") != VERSION
            or value.get("task_id") != task["task_id"]
            or value.get("executor") != task["executor"]
            or set(value.get("bound_sha256", {})) != set(BOUND_FILES)
            or not isinstance(value.get("history"), list)
            or value.get("status") not in {"prepared", "sending", "sent_verified", "uncertain"}):
        raise ValueError("Delivery identity/schema mismatch; inspect the task without sending")
    return value


def _binding(job, delivery):
    current = _hashes(job)
    changed = [name for name in BOUND_FILES if current[name] != delivery["bound_sha256"][name]]
    if changed:
        raise ValueError("Prepared task files changed; do not send: " + ", ".join(changed))
    message = delivery.get("handoff_text")
    if not isinstance(message, str) or hashlib.sha256(message.encode("utf-8")).hexdigest() != delivery.get("message_sha256"):
        raise ValueError("Prepared handoff payload changed; do not send")


def _queued(state):
    if state["state"] != "queued":
        raise ValueError("Only an unclaimed queued task can start or release a delivery attempt")


def _event(delivery, name, **fields):
    timestamp = d.now()
    delivery["updated_at"] = timestamp
    delivery["history"].append({"event": name, "at": timestamp, **fields})


def prepare(board, task_id):
    """Bind immutable task files; repeatable only while no attempt is active."""
    job, _, _ = _context(board, task_id)
    with d.exclusive(job / ".operation.lock"):
        _, task, state = _context(board, task_id)
        _queued(state)
        delivery = _load(job, task)
        if delivery is not None:
            _binding(job, delivery)
            if delivery["status"] != "prepared":
                raise ValueError("Delivery already started; inspect status and do not resend")
            return delivery
        hashes = _hashes(job)
        message = (job / "HANDOFF.txt").read_text(encoding="utf-8-sig").rstrip("\r\n")
        if not message.strip():
            raise ValueError("HANDOFF.txt is empty")
        delivery = {
            "schema_version": VERSION, "task_id": task_id, "executor": task["executor"],
            "transport": "computer-use", "delivery_id": uuid.uuid4().hex,
            "status": "prepared", "prepared_at": d.now(), "bound_sha256": hashes,
            "handoff_file": str(job / "HANDOFF.txt"), "handoff_text": message,
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            "attempt": None, "history": [],
        }
        _event(delivery, "prepared", message_sha256=delivery["message_sha256"])
        d.write_json(job / DELIVERY_FILE, delivery)
        return delivery


def begin(board, task_id, app, window, conversation, evidence, transport="computer-use"):
    """Persist a unique attempt before any UI typing or send operation."""
    if transport not in {"computer-use", "local-desktop"}:
        raise ValueError("Unsupported desktop delivery transport")
    target = {"app": _text(app, "Target app"), "window": _text(window, "Target window"),
              "conversation": _text(conversation, "Target conversation")}
    evidence = _text(evidence, "Target observation")
    job, _, _ = _context(board, task_id)
    with d.exclusive(job / ".operation.lock"):
        _, task, state = _context(board, task_id)
        _queued(state)
        delivery = _load(job, task)
        if delivery is None:
            raise ValueError("Prepare the delivery before beginning an attempt")
        _binding(job, delivery)
        if delivery["status"] != "prepared" or delivery.get("attempt") is not None:
            raise ValueError("A delivery attempt already exists; inspect it and do not resend")
        attempt = {"attempt_id": uuid.uuid4().hex, "started_at": d.now(), "transport": transport,
                   "target": target, "target_evidence": evidence}
        delivery["status"] = "sending"
        delivery["transport"] = transport
        delivery["attempt"] = attempt
        _event(delivery, "began", **attempt)
        d.write_json(job / DELIVERY_FILE, delivery)
        return delivery


def _attempt(delivery, attempt_id):
    if delivery is None:
        raise ValueError("No delivery was prepared")
    attempt = delivery.get("attempt")
    if not isinstance(attempt, dict) or attempt.get("attempt_id") != attempt_id:
        raise ValueError("Attempt ID does not match the active delivery; do not overwrite it")
    return attempt


def record(board, task_id, attempt_id, outcome, evidence):
    """Record UI evidence only, including after a concurrent claim/cancellation.

    This never changes task state, authorizes another send, or implies receipt.
    An uncertain attempt may be resolved to sent_verified by later observation.
    """
    if outcome not in {"sent_verified", "uncertain"}:
        raise ValueError("Outcome must be sent_verified or uncertain")
    evidence = _text(evidence, "Send observation")
    job, _, _ = _context(board, task_id)
    with d.exclusive(job / ".operation.lock"):
        _, task, _ = _context(board, task_id)
        delivery = _load(job, task)
        attempt = _attempt(delivery, attempt_id)
        if delivery["status"] == outcome and attempt.get("send_evidence") == evidence:
            return delivery
        if delivery["status"] not in IN_FLIGHT:
            raise ValueError("This attempt already has verified send evidence; it cannot be reset")
        delivery["status"] = outcome
        attempt.update(outcome=outcome, send_evidence=evidence, recorded_at=d.now())
        _event(delivery, "recorded", attempt_id=attempt_id, outcome=outcome, evidence=evidence)
        d.write_json(job / DELIVERY_FILE, delivery)
        return delivery


def release_unsent(board, task_id, attempt_id, evidence, confirmed_unsent=False):
    """Allow another attempt only after an explicit observed non-send.

    No elapsed time, absent claim, or absent report constitutes this evidence.
    Verified sends cannot be released. Preserve the old attempt in history.
    """
    if confirmed_unsent is not True:
        raise ValueError("Explicit confirmed_unsent is required; a timeout is not evidence")
    evidence = _text(evidence, "Observed non-send evidence")
    job, _, _ = _context(board, task_id)
    with d.exclusive(job / ".operation.lock"):
        _, task, state = _context(board, task_id)
        _queued(state)
        delivery = _load(job, task)
        attempt = _attempt(delivery, attempt_id)
        if delivery["status"] not in IN_FLIGHT:
            raise ValueError("Only an unverified sending/uncertain attempt may be released as unsent")
        _binding(job, delivery)
        _event(delivery, "released_unsent", attempt_id=attempt_id, evidence=evidence,
               confirmed_unsent=True, previous_attempt=attempt)
        delivery["status"] = "prepared"
        delivery["attempt"] = None
        d.write_json(job / DELIVERY_FILE, delivery)
        return delivery


def status(board, task_id):
    """Read delivery and authoritative execution state without changing either."""
    job, _, _ = _context(board, task_id)
    with d.exclusive(job / ".operation.lock"):
        _, task, state = _context(board, task_id)
        delivery = _load(job, task)
        binding_matches = None
        binding_error = None
        if delivery is not None:
            try:
                _binding(job, delivery)
                binding_matches = True
            except (OSError, ValueError) as exc:
                binding_matches = False
                binding_error = str(exc)
        report_valid = False
        report_error = None
        if (job / "RESULT.json").exists():
            try:
                d.validate_report(d.read_json(job / "RESULT.json"), task, state)
                report_valid = True
            except (OSError, ValueError, KeyError) as exc:
                report_error = str(exc)
        execution = state["state"]
        acknowledged = bool(state.get("claim_token") and state.get("executor") == task["executor"])
        ready = execution in d.REPORT_STATES and report_valid
        if execution in d.TERMINAL:
            action = "done" if execution == "accepted" else "task_closed"
        elif execution == "cancel_requested":
            action = "confirm_worker_stopped_then_review"
        elif execution in d.REPORT_STATES:
            action = "collect_and_review" if ready else "inspect_invalid_report"
        elif execution == "claimed":
            action = "wait_for_report"
        elif binding_matches is False:
            action = "inspect_binding_change_do_not_send"
        elif delivery is None:
            action = "prepare_delivery"
        elif delivery["status"] == "prepared":
            action = "verify_target_then_begin"
        elif delivery["status"] == "sent_verified":
            action = "wait_for_claim_do_not_resend"
        else:
            action = "inspect_target_and_task_do_not_resend"
        return {
            "task_id": task_id, "executor": task["executor"], "execution_state": execution,
            "delivery_status": delivery["status"] if delivery else "not_prepared",
            "attempt_id": (delivery.get("attempt") or {}).get("attempt_id") if delivery else None,
            "worker_acknowledged": acknowledged, "report_valid": report_valid,
            "ready_to_collect": ready, "accepted_by_gpt": execution == "accepted",
            "binding_matches": binding_matches, "binding_error": binding_error,
            "report_error": report_error, "next_action": action,
            "delivery_file": str(job / DELIVERY_FILE),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "begin", "record", "status", "release-unsent"):
        command = commands.add_parser(name)
        command.add_argument("--task-id", required=True)
        if name == "begin":
            for field in ("app", "window", "conversation", "evidence"):
                command.add_argument("--" + field, required=True)
        elif name in {"record", "release-unsent"}:
            command.add_argument("--attempt-id", required=True)
            command.add_argument("--evidence", required=True)
            if name == "record":
                command.add_argument("--outcome", choices=("sent_verified", "uncertain"), required=True)
            else:
                command.add_argument("--confirmed-unsent", action="store_true")
    args = parser.parse_args(argv)
    board = args.board.expanduser().resolve()
    try:
        if args.command == "prepare":
            result = prepare(board, args.task_id)
        elif args.command == "begin":
            result = begin(board, args.task_id, args.app, args.window, args.conversation, args.evidence)
        elif args.command == "record":
            result = record(board, args.task_id, args.attempt_id, args.outcome, args.evidence)
        elif args.command == "release-unsent":
            result = release_unsent(board, args.task_id, args.attempt_id, args.evidence, args.confirmed_unsent)
        else:
            result = status(board, args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Desktop delivery error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
