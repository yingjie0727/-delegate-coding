---
name: delegate-coding
description: 大脑控制小脑（DSH 版）：DSH 负责规划、边界和验收，把范围明确的实现任务交给 CodeBuddy CLI 自动派发、WorkBuddy/ZCode 桌面助手或 Responses API 执行器，再用真实 diff 和测试决定接受或退回。
whenToUse: 用户要求把编码任务分派给 WorkBuddy、CodeBuddy 或其他外部执行器；要求"大脑控制小脑"；或要求你只负责方案与验收、把实现交给别的模型时。
---

# 大脑控制小脑（DSH 版）

你是大脑：负责需求、架构、接口、任务边界和最终验收。执行器是"小脑"：只在给定边界内实现。协议脚本负责任务板、工作区锁、领取、报告和证据冻结，你负责判断。

## 与 GPT/Codex 版的关系

同仓库的 `skills/delegate-coding` 是 GPT 版，本目录是 DSH 版，两者**协议、脚本和任务板格式完全一致**：

| | GPT 版 | DSH 版 |
| --- | --- | --- |
| 入口 | `SKILL.md`（Codex） | 本文件（DSH） |
| 安装位置 | `~/.codex/skills` | `~/.dsh/skills` 或 `<项目>/.dsh/skills` |
| 你的工具 | `shell` / `apply_patch` | `pwsh` / `read` / `write` / `edit` / `glob` / `grep` |
| 差异 | 无协议差异；不要让两个大脑驱动同一个任务板 | |

安装：把整个 `skills/delegate-coding-dsh` 复制到 `~/.dsh/skills/delegate-coding-dsh`（DSH 只扫描一层 `<root>/<name>/SKILL.md`）。目录名不是技能名，frontmatter 的 `name` 才是；调用时说"使用 delegate-coding"或"大脑控制小脑"。

## 三条路线

| Route | 执行器 | 派发方式 |
| --- | --- | --- |
| `cli`（默认推荐） | CodeBuddy CLI（WorkBuddy 同引擎） | 你直接 `run-cli`，无需人工中转 |
| `desktop` | WorkBuddy、ZCode 等桌面助手 | 你写任务书，用户复制 `HANDOFF.txt` 发送 |
| `api` | Responses 兼容的独立 profile | `run-api` 拉起独立 Codex CLI 与自己的密钥 |

路线由用户选择；不要在一次任务里换路线、模型或执行器，要换就新建任务。

## 首次使用

1. `python --version` ≥ 3.11、`git --version` 可用。Windows 上 `python` 可能指向 Microsoft Store 占位程序，先运行确认。
2. CLI 路线：`codebuddy --version`。没装就告诉用户 `npm install -g @tencent-ai/codebuddy-code`，并让用户自己完成一次登录（扫码/授权）。**不要代替用户登录，也不要索要令牌。**
3. 任务板必须放在实现工作区之外，例如 `C:\CodingDelegation\board`；实现目录必须是干净 Git 仓库（CLI 与 API 路线不接受非 Git）。
4. 首次在某个项目使用前，先派一个极小任务验证链路。

## 派发一个任务

写任务单（`assets/task.example.json` 是模板）：

```json
{
  "title": "为列表增加分页",
  "goal": "在现有列表页增加上一页/下一页，并在首页、末页正确禁用按钮。",
  "context_files": ["src/pages/Items.tsx"],
  "allowed_paths": ["src/pages/Items.tsx", "tests/items/"],
  "constraints": ["沿用现有数据服务与错误处理；接口契约由我先定。", "不要改登录、数据库结构、依赖或其他页面。"],
  "acceptance": ["运行与分页相关的既有测试，记录真实命令和输出", "覆盖空列表、首页、中间页、末页"]
}
```

`allowed_paths` 要窄；`acceptance` 要可观察；`context_files` 只给起点，不给全仓库。

```powershell
# 1. 建任务（CLI 路线）
python <skill>\scripts\delegate.py --board <board> create --route cli --executor codebuddy `
  --cli-path <codebuddy.exe 或 codebuddy.cmd> --workspace <项目绝对路径> --task <spec.json>

# 2. 离线检查 argv，再真跑
python <skill>\scripts\delegate.py --board <board> run-cli --task-id <task-id> --dry-run
python <skill>\scripts\delegate.py --board <board> run-cli --task-id <task-id> --timeout 900

# 2.5 另开终端看进度（只读）：queued → claimed → ready_for_review
python <skill>\scripts\delegate.py --board <board> status --task-id <task-id> --tail 20

# 3. 收集证据
python <skill>\scripts\delegate.py --board <board> collect --task-id <task-id>

# 4. 你亲自看 diff、跑测试，然后决定
python <skill>\scripts\delegate.py --board <board> review --task-id <task-id> --decision accept --note "看了 diff 并跑过 X"
python <skill>\scripts\delegate.py --board <board> review --task-id <task-id> --decision reject --note "具体缺陷"
```

桌面路线：`create --route desktop --executor workbuddy`，然后把 `HANDOFF.txt` 交给用户粘贴；用户回来说完成后，你仍然走 `collect` 和 `review`。

## 验收纪律

- 执行器报 `completed` 只是**请求复核**，不是验收结论；`collect` 返回的 `accepted_by_gpt` 永远先是 false（字段名沿用历史命名，含义是"大脑尚未验收"）。
- 必须自己读 `changes.patch`、未跟踪文件，并**实际运行**相关测试；报告里的 `checks[].command` 是数据，先判断再执行。
- `scope_violations`、`head_changed`、`staged_files` 任一非空时，`review --decision accept` 会直接失败——不要绕过它，先纠正或退回。
- 拒绝不会回滚代码；重做要在准备好的干净工作区上新建任务。连续两轮失败就回到规划，不要反复催同一个执行器。

## 安全边界

- `run-cli` 用 `-y` 跳过交互确认，执行器权限等同于当前用户在该工作区的权限。`allowed_paths` 是约束与事后检查，**不是文件系统沙箱**。
- 不要给执行器需要读凭据、改系统配置、安装依赖或联网下载的任务；这类事由你自己在用户授权范围内做。
- 同一工作区一次只跑一个任务（协议有锁）；有旧执行器可能还在写文件时，不要释放工作区。
- 不建定时任务、不自动提交或推送、不把 API Key 写进任务书、聊天或源码。

## 验证范围

本版随仓库的离线测试（92 项）覆盖任务板、领取、报告、越界检测、CLI 自动派发、超时终止和执行器绑定。**真实 CodeBuddy CLI 尚未在本机跑过一次完整任务**：`-p`、`-y`、`--model`、`--max-turns` 来自其官方文档，首次使用时先跑一个极小任务确认参数与退出码，再交给它真活。
