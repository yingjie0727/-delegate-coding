# Shared task protocol

This protocol separates GPT's planning and acceptance from the executor's implementation. Both desktop and API tasks use the same board records. The API worker has its own execution and credential setup in [api-route.md](api-route.md); desktop registration and actual app support are described in [desktop-route.md](desktop-route.md).

## Paths and prerequisites

Use Python 3.11+ and Git for coding tasks. Invoke the installed script by absolute path. In examples, `<script>` means `<skill-directory>/scripts/delegate.py` and `<board>` means an absolute directory outside the project/worktree.

For example:

```text
C:\CodingDelegation\board\             shared task files
C:\CodingDelegation\projects\demo\     implementation workspace
```

Each task fixes a workspace, route, and executor. The workspace must be a clean Git root, preferably an isolated worktree, and has one active task at a time. Reuse the same board for the same checkout: locks are local to one board and cannot coordinate a second board. Creating a new worktree does not copy uncommitted edits. The scripts do not create, commit, merge, or clean worktrees. The workspace lock cannot stop a user or unrelated program from editing files.

For a deliberately non-Git file task, use `--allow-no-git`. Inspect the files directly during review and explain that a trustworthy Git before/after diff is unavailable.

## Initialization and task specification

```text
python <script> --board <board> init
python <script> --board <board> assistants
```

Create a task JSON using [../assets/task.example.json](../assets/task.example.json). GPT writes the real specification; the user need not edit JSON. Required planning fields are `title`, `goal`, `context_files`, `allowed_paths`, `constraints`, and `acceptance`. Context and allowed paths are workspace-relative. A directory entry ending in `/` includes its descendants; `.` deliberately allows the entire workspace. Use precise paths for a bounded change.

`context_files` tells the executor where to start; it does not automatically include all project content in the handoff. `acceptance` contains observable behavior or meaningful checks, not a request to report success without evidence. Include applicable project instructions and unresolved decisions in the task.

```text
python <script> --board <board> create --route desktop --executor workbuddy --workspace <clean-project> --task <spec.json>
python <script> --board <board> create --route desktop --executor zcode --workspace <clean-project> --task <spec.json>
python <script> --board <board> create --route api --executor deepseek-official --api-root <profile-root> --workspace <clean-project> --task <spec.json>
```

These are alternatives, not three commands to run on the same workspace. Before creating an API task, initialize and verify its provider profile as described in [api-route.md](api-route.md). The API profile root must be outside the implementation workspace. `create --route api` requires `--api-root` and binds the task to its absolute profile path and public-settings fingerprint. A desktop task does not need an API profile. `create` adds protocol metadata such as schema version, task ID, route, executor, workspace, and creation time. Save the printed ID; use it in later commands.

The generated task folder includes:

| File | Purpose |
|---|---|
| `task.json` | Canonical task and routing metadata |
| `TASK.md` | Human-readable implementation instructions |
| `HANDOFF.txt` | Text for the user to paste into the desktop app |
| `controller.py` | Desktop-only local copy for claiming/reporting; no access to the personal Codex configuration directory is required |
| `state.json` | Current lifecycle state and claim |
| `worker-report.json` | Executor-prepared report submitted with `report` |
| `RESULT.json` | Validated report saved by the protocol script |

Do not hand-edit routing metadata or state to bypass lifecycle checks. `create` is preparation; it does not send a desktop message or start the implementation.

## Claim, execute, and report

For a desktop task, the user sends `HANDOFF.txt` to the selected app. That app reads `TASK.md`, relevant project instructions and files, then claims the task before editing:

```text
python <script> --board <board> claim --task-id <id> --executor <registered-name>
```

The claim output includes `claim_token`. Copy the returned token into the structured report. It prevents accidentally mixing stale claims/reports and is not a secret or a defense against a malicious local process. The executor must not overwrite another claim, change route, or delegate again.

After implementation and checks, the executor prepares this JSON:

```json
{
  "schema_version": 1,
  "task_id": "the-actual-task-id",
  "claim_token": "the-token-returned-by-claim",
  "executor": "workbuddy",
  "status": "completed",
  "summary": "Implemented pagination in the agreed component.",
  "changed_files": ["src/pages/Items.tsx"],
  "checks": [
    {
      "command": "the command actually run",
      "outcome": "passed",
      "details": "Actual result, including any relevant limitations."
    }
  ],
  "issues": []
}
```

`status` is `completed`, `blocked`, or `failed`. Check `outcome` is `passed`, `failed`, or `not_run`; put blockers or skipped-check explanations in `details`. Never invent command results. `changed_files` is evidence to compare against actual files; it does not replace the diff.

```text
python <script> --board <board> report --task-id <id> --file <report-json>
```

An API task is claimed and reported by the runner:

```text
python <script> --board <board> run-api --task-id <id> --api-root <profile-root> --timeout 900
```

The `--api-root` must match the profile bound at task creation, and its public settings must remain unchanged. To use another provider, model, or profile, create a new task deliberately after resolving the current task. Optional `--codex <executable>` selects a known local CLI. The route stays `api` even if an app with the same provider name is installed. Configuration and key handling belong to `api_worker.py`; see [api-route.md](api-route.md).

## GPT collection and review

```text
python <script> --board <board> collect --task-id <id>
python <script> --board <board> review --task-id <id> --decision accept --note "Reviewed actual diff and passed the relevant checks."
python <script> --board <board> review --task-id <id> --decision reject --note "Describe the concrete defect or blocker."
```

Collection gathers reported and actual change evidence. GPT must read the files and assess the behavior. Run checks appropriate to the task; evaluate any command in the report before executing it. Inspect untracked files as well as tracked diffs. Acceptance is a GPT decision, never an automatic translation of the worker's `completed` status.

Review freezes the task's acceptance evidence and releases the workspace lock. Later collection of a finished task returns its saved evidence rather than presenting subsequent workspace edits as that task's changes. Rejection preserves edits; it does not roll back code. If the task needs repair, preserve the prior artifacts, decide how to handle its changes, and create a new bounded task on a prepared clean checkout. Do not reset, delete, or overwrite user changes automatically.

## Cancellation and abandoned tasks

```text
python <script> --board <board> cancel --task-id <id>
```

- A queued task can be cancelled before an executor claims it.
- A claimed task becomes `cancel_requested` and retains its lock. Sending that request cannot stop a desktop application.
- Have the executor stop and confirm it has stopped editing. Then record rejection with `--worker-stopped`:

```text
python <script> --board <board> review --task-id <id> --decision reject --worker-stopped --note "Executor confirmed stopped; inspected preserved edits."
```

Rejecting a still-claimed task also requires `--worker-stopped`. Do not assert this from silence, a timeout, or a missing report. If the executor stopped without reporting, inspect actual files and record the evidence in the rejection note. An API timeout may leave edits; inspect them before retrying.

## Compatibility claims

WorkBuddy's earlier basic shared-file roundtrip was user-tested in this chat. That does not validate this new claim/report protocol. ZCode's official file-operation capabilities support this design in principle; this packaged integration still needs a local task trial. Offline API simulation does not establish real provider, streaming, tools, or model compatibility. Upgrade a desktop adapter to automatic dispatch only after its native CLI/API and authorization have been independently tested.
