---
name: delegate-coding
description: 大脑控制小脑。由 GPT 规划和验收，将明确的实现任务交给 WorkBuddy 等桌面助手、独立 Responses API 执行器或 CodeBuddy CLI。用于派发任务、收集结果和核对实际改动，按用户选择的路线与执行器推进。
---

# 大脑控制小脑

GPT 负责规划和验收，选定的执行器负责实现。使用 `$delegate-coding` 或“大脑控制小脑”调用。

## 本机运行路径

首次配置 API 路线时，按 [运行路径配置](references/runtime-paths.md) 定位并记录这台电脑已验证的 Python、Codex CLI 和私有 profile。随包脚本从本 Skill 所在目录解析。后续使用已记录的位置，不要求用户每次手输绝对路径。共享源码只保留通用方法，个人路径记录属于本机安装副本。

Keep requirements, architecture, interfaces, task boundaries, and final acceptance in the GPT conversation. Choose one execution route per task:

| Route | Executor | Handoff |
|---|---|---|
| `desktop` | WorkBuddy, ZCode, or another registered desktop assistant | Send `HANDOFF.txt` through the selected desktop control or manual handoff, then collect the worker's files. |
| `api` | A named Responses-compatible provider profile | The script runs a separate Codex CLI worker with its own configuration and API key. |
| `cli` | CodeBuddy Code (`codebuddy`) | The controller launches the bound CLI in the clean Git workspace and validates the report and changes. |

Use each executor's own login or provider configuration. Keep the route, model, provider, and executor fixed for each task. Confirm delivery and results using observed evidence.

Read [the shared protocol](references/task-protocol.md) for task states, commands, cancellation, and review. Load the needed route reference: [desktop](references/desktop-route.md), [WorkBuddy desktop control](references/desktop-control.md), [API](references/api-route.md), or [CodeBuddy CLI](references/cli-route.md).

## Prepare and dispatch

1. Inspect the actual project and its applicable instructions. Plan a bounded task with concrete acceptance checks. Retain unclear or cross-cutting design decisions in GPT. Reuse the user's selected route and executor; ask only if a necessary choice cannot be inferred.
2. Use a clean Git checkout or worktree with no competing writer. The task board must be outside the implementation workspace. Always reuse the same board for the same checkout: the workspace lock coordinates only tasks in that board and cannot stop an external editor. One unfinished task locks that workspace until GPT records a review. For a non-Git file task, explicitly use `--allow-no-git` and explain that Git cannot independently establish the changes. Do not discard existing edits to obtain a clean checkout.
3. Write a task specification based on [assets/task.example.json](assets/task.example.json): `title`, `goal`, `context_files`, `allowed_paths`, `constraints`, and `acceptance`. Use repository-relative paths. Scope rules are instructions and checks, not operating-system access controls. Send only task-relevant context; do not include credentials.
4. Use `scripts/delegate.py --board <absolute-board-directory> create --route <desktop|api|cli> --executor <name> --workspace <absolute-project-directory> --task <spec.json>`. Bind the provider profile for `api` or the executable and optional model for `cli` as described in the route reference. Preserve the returned task ID and task folder.
5. For authorized WorkBuddy desktop control, first record the attempt, then verify the sent message and wait for the worker's claim. For manual delivery, supply the generated `HANDOFF.txt`. Check the original conversation and task state before retrying an interrupted send.
6. For `api`, first verify the native Windows executor without a model request as described in the API reference when setting up or changing that executor. Run `scripts/delegate.py --board <board> run-api --task-id <id> --api-root <profile-root>` using the exact profile bound at creation with unchanged public settings. This can send task text and model-selected project content to the configured provider and incur charges. Respect existing delegation authorization. Keep normal command session/polling support for progress; do not create a scheduled job or change the main Codex login, config, or model catalog.
7. For `cli`, verify the CodeBuddy version and run `scripts/delegate.py --board <board> run-cli --task-id <id>`. Use `status --task-id <id> --plain --tail 0` to read progress. Check the report and the observed Git changes before deciding whether to accept.

## Collect and accept

- Desktop workers claim and submit directly. The CLI and API controllers record their worker's claim and submission using the same task protocol.
- Run `collect --task-id <id>`. Read the worker report, actual Git diff, untracked files, and checks. A worker's `completed` report is a request for review. It does not mean GPT has accepted the implementation.
- Independently assess the diff and execute appropriate acceptance checks. Evaluate report commands before executing them. Confirm allowed paths, behavior, and unresolved issues. For a non-Git task, inspect outputs directly and report the weaker change evidence.
- Record `review --task-id <id> --decision accept|reject --note <reason>`. Rejected changes remain on disk. Prepare the next clean checkout deliberately before another implementation task; do not reset or delete work without authorization. After two unsuccessful repair rounds, revisit the plan in GPT.
- `cancel` cannot stop a desktop assistant. For a claimed task it requests cancellation and retains the workspace lock. Confirm that the executor stopped, then reject with `--worker-stopped`. Never release the workspace while an old worker may still edit it.

State which executor actually ran, what GPT verified, which commands passed or failed, and remaining limits. Do not infer tool access, completion, cost savings, or verification from registration or an assistant's summary alone.
