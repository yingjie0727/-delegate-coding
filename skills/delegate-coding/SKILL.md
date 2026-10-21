---
name: delegate-coding
description: 大脑控制小脑：由 GPT 负责规划和验收，将范围明确的实现任务交给 WorkBuddy、ZCode 等桌面助手，或独立的 Responses API 执行器。用于分派编码任务、交接、收集结果和检查实际改动，保留用户选择的路线及执行器。
---

# 大脑控制小脑

GPT 是负责规划和验收的“大脑”，桌面助手或 API 模型是负责具体实现的“小脑”。内部调用名保持 `$delegate-coding`，也可以直接要求使用“大脑控制小脑”。

## 本机运行路径

首次配置 API 路线时，按 [运行路径配置](references/runtime-paths.md) 定位并记录这台电脑已验证的 Python、Codex CLI 和私有 profile。随包脚本从本 Skill 所在目录解析。后续使用已记录的位置，不要求用户每次手输绝对路径。共享源码只保留通用方法，个人路径记录属于本机安装副本。

Keep requirements, architecture, interfaces, task boundaries, and final acceptance in the GPT conversation. Choose one execution route per task:

| Route | Executor | Handoff |
|---|---|---|
| `desktop` | WorkBuddy, ZCode, or another registered desktop assistant | GPT writes task files; the user pastes `HANDOFF.txt` into that assistant. |
| `api` | A named Responses-compatible provider profile | The script runs a separate Codex CLI worker with its own configuration and API key. |

Desktop logins do not grant API access. An API profile does not control the desktop app. Do not switch routes, models, providers, or executors as a hidden fallback. Registration means a reusable file-handoff template, not verified automatic dispatch. Both routes are external workers, not native Codex desktop subagents.

Read [references/task-protocol.md](references/task-protocol.md) for task states, command examples, cancellation, and review. For desktop setup, read [references/desktop-route.md](references/desktop-route.md). For provider setup and credentials, read [references/api-route.md](references/api-route.md). Load only the route needed.

## Prepare and dispatch

1. Inspect the actual project and its applicable instructions. Plan a bounded task with concrete acceptance checks. Retain unclear or cross-cutting design decisions in GPT. Reuse the user's selected route and executor; ask only if a necessary choice cannot be inferred.
2. Use a clean Git checkout or worktree with no competing writer. The task board must be outside the implementation workspace. Always reuse the same board for the same checkout: the workspace lock coordinates only tasks in that board and cannot stop an external editor. One unfinished task locks that workspace until GPT records a review. For a non-Git file task, explicitly use `--allow-no-git` and explain that Git cannot independently establish the changes. Do not discard existing edits to obtain a clean checkout.
3. Write a task specification based on [assets/task.example.json](assets/task.example.json): `title`, `goal`, `context_files`, `allowed_paths`, `constraints`, and `acceptance`. Use repository-relative paths. Scope rules are instructions and checks, not operating-system access controls. Send only task-relevant context; do not include credentials.
4. Use `scripts/delegate.py --board <absolute-board-directory> create --route <desktop|api> --executor <name> --workspace <absolute-project-directory> --task <spec.json>`. For `api`, first initialize/verify the provider profile outside the implementation workspace, and add `--api-root <profile-root>` at creation. This binds the task to that absolute profile path and its public settings. Preserve the returned task ID and task folder.
5. For `desktop`, present the generated `HANDOFF.txt` for the user to paste into the chosen app. Do not claim the task was sent, started, or completed merely because files exist. Authorize access only to the necessary board and project paths. If an app accepts one directory, select their common parent and keep the task's allowed paths narrow.
6. For `api`, first verify the native Windows executor without a model request as described in the API reference when setting up or changing that executor. Run `scripts/delegate.py --board <board> run-api --task-id <id> --api-root <profile-root>` using the exact profile bound at creation with unchanged public settings. This can send task text and model-selected project content to the configured provider and incur charges. Respect existing delegation authorization. Keep normal command session/polling support for progress; do not create a scheduled job or change the main Codex login, config, or model catalog.

## Collect and accept

- A worker first claims its task, then submits a structured report using the returned `claim_token`. The token binds a report to its claim; it is visible in task state and is not an authentication secret.
- Run `collect --task-id <id>`. Read the worker report, actual Git diff, untracked files, and checks. A worker's `completed` report is a request for review. It does not mean GPT has accepted the implementation.
- Independently assess the diff and execute appropriate acceptance checks. Evaluate report commands before executing them. Confirm allowed paths, behavior, and unresolved issues. For a non-Git task, inspect outputs directly and report the weaker change evidence.
- Record `review --task-id <id> --decision accept|reject --note <reason>`. Rejected changes remain on disk. Prepare the next clean checkout deliberately before another implementation task; do not reset or delete work without authorization. After two unsuccessful repair rounds, revisit the plan in GPT.
- `cancel` cannot stop a desktop assistant. For a claimed task it requests cancellation and retains the workspace lock. Confirm that the executor stopped, then reject with `--worker-stopped`. Never release the workspace while an old worker may still edit it.

State which executor actually ran, what GPT verified, which commands passed or failed, and remaining limits. Do not infer tool access, completion, cost savings, or verification from registration or an assistant's summary alone.
