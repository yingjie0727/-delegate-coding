# 大脑控制小脑

一个可双大脑驱动的 Skill：GPT（Codex）或 DeepSeek Harness（DSH）负责规划、拆任务和验收，把范围明确的实现交给外部执行器。两种安装形态在同一仓库并行，**协议、脚本和任务板格式完全一致**：

| 版本 | 目录 | 调用名 | 安装位置 |
| --- | --- | --- | --- |
| GPT 版 | `skills/delegate-coding` | `$delegate-coding` | `%USERPROFILE%\.codex\skills` |
| DSH 版 | `skills/delegate-coding-dsh` | `delegate-coding` | `~/.dsh/skills` 或 `<项目>/.dsh/skills` |

显示名称：**大脑控制小脑**。上传版本更新于 2026-11-15；真实小任务验证日期为 2026-10-16。

## 三条路线

| 路线 | 执行器 | 使用方式 |
| --- | --- | --- |
| 桌面助手 | WorkBuddy、ZCode，以及其他能按协议读写本地文件的助手 | 大脑生成交接文字，用户粘贴发送；助手实现并写回报告，大脑验收 |
| **CLI（自动派发）** | CodeBuddy CLI，WorkBuddy 同引擎 | `run-cli` 直接拉起进程，无需人工中转；执行器自己领取和报告 |
| API | 已配置的 Responses API 模型，本机已测试 DeepSeek 官方 `deepseek-flash` | 独立 Codex CLI 执行器调用模型，收集改动，大脑独立检查 |

Skill 不修改主 Codex 登录态或模型菜单。三条路线共用同一套任务协议；登记助手模板不等于自动接通桌面软件。CLI 路线首次创建任务时会把可执行文件绝对路径写进任务，之后**不能换成另一个执行器**。

## 安装

GPT 版：把本仓库链接交给 Codex，并说：

> 请安装这个仓库中的 skills/delegate-coding 技能，保留我已有的配置和修改。

DSH 版：把整个 `skills/delegate-coding-dsh` 文件夹复制到 `~/.dsh/skills/`（Windows 上是 `%USERPROFILE%\.dsh\skills`），或复制到项目的 `.dsh/skills/`。DSH 只扫描一层 `<root>/<name>/SKILL.md`，目录名不必等于技能名。

两个版本不要同时驱动同一个任务板：任务板里的工作区锁只协调它自己记录的任务，不阻止另一个大脑再建一个。

需要 Python 3.11+ 和 Git。API 路线另需 Codex CLI、自己的厂商 API 密钥与可用额度；CLI 路线另需 `npm install -g @tencent-ai/codebuddy-code` 并交互式登录一次。Windows 首次调用 API 模型前需要显式配置本机沙箱实现，并通过不调用 API 的执行环境检查。

## 首次配置后记住路径

首次使用 API 或 CLI 路线时，大脑会定位随包脚本、Python、Codex CLI 或 CodeBuddy CLI，核对后记录到这台电脑的本机安装副本。以后复用记录，无需每项任务都手输执行器的绝对路径。应用升级或文件移动后重新核对并更新记录。

共享版本只包含通用配置方法，不包含开发机的绝对路径、密钥或登录信息。更新已安装 Skill 时先保留自己的本机路径区段和其他修改。

## 使用

桌面路线：

> 使用“大脑控制小脑”，你负责方案和验收，走桌面路线，交给 WorkBuddy 实现。我的需求是……

CLI 路线（无需人工粘贴）：

> 使用 delegate-coding，走 CLI 路线，用 CodeBuddy CLI 自动派发。先 dry-run 确认参数，再派一个很小的任务。我的需求是……

API 路线：

> 使用 $delegate-coding，走 DeepSeek 官方 API 路线。先检查独立配置和本机执行环境，再做一个小任务验证。密钥只在本机输入，不要让我发到聊天。我的需求是……

## 已验证到哪一步

2026-10-16 的开发机实测：

- WorkBuddy 桌面：一次完整的领取、实现、报告、GPT 验收小任务通过，8 项测试通过；仍由用户复制发送交接文字。
- DeepSeek 官方 API：真实模型响应、命令工具、文件修改和正式报告均完成，GPT 独立运行 8 项测试通过；本机执行环境的项目内读写与项目外写入拒绝检查通过。
- API 实测由用户启动本机入口，随后自动执行，GPT 验收。当前隔离会话直接全自动发起尚未验证。

2026-11-15 新增：

- CLI 自动派发（`--route cli`）：领取、实现、报告、未报告、超时终止、可执行文件绑定、越界检查均有离线测试覆盖；另用真实 CodeBuddy CLI 2.184.0 验证了 `--dry-run` 参数与派发失败路径——**成功路径尚未验证**（本机未登录）。一个真实发现：未登录时 CLI 打印认证错误却以退出码 0 结束，协议按"没有报告"记为失败，这正是不能只看退出码的原因。
- DSH 版技能：与 GPT 版共用同一套脚本，仓库内有一致性测试（`test_bundle_parity.py`），任何一边被改而另一边没同步都会导致测试失败。
- 离线测试共 89 项全部通过。

- ZCode：已有共享文件模板，尚未完成本机任务实测。Aider、MCP 自动派发和原生跨厂商子代理尚未集成。

这些结果验证了两个真实小任务加一组离线协议测试，不代表所有设备、模型、长上下文或复杂项目都已验证。

## 文档

- [小白使用说明](docs/使用说明.md)
- [验证记录与限制](docs/验证记录.md)
- [共享任务协议](skills/delegate-coding/references/task-protocol.md)
- [桌面路线](skills/delegate-coding/references/desktop-route.md)
- [CLI 自动派发路线](skills/delegate-coding/references/cli-route.md)
- [API 配置与执行环境检查](skills/delegate-coding/references/api-route.md)
- [运行路径配置与复用](skills/delegate-coding/references/runtime-paths.md)

离线测试：

```text
python -B -m unittest discover -s skills/delegate-coding/scripts -p "test_*.py" -v
```

## 分享内容

仓库只包含通用 Skill 源码和说明。每个使用者在自己的电脑配置 API 或 CLI；不共享开发机的密钥、加密凭据、本机 profile、登录文件、任务目录和运行日志。DSH 版是 GPT 版的同源副本（脚本与协议文档逐字节一致），由仓库内一致性测试保证不漂移。

仓库已有 `LICENSE` 时请保留；本包不替仓库所有者选择或更改许可证。
