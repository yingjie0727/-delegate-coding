# 大脑控制小脑

一个 Codex Skill：GPT 负责规划、拆任务和验收，将范围明确的代码实现交给外部执行器。

显示名称：**大脑控制小脑**。内部调用名：`$delegate-coding`。

上传版本更新于 2026-10-21；真实小任务验证日期为 2026-10-16。

## 两条路线

| 路线 | 执行器 | 使用方式 |
| --- | --- | --- |
| 桌面助手 | WorkBuddy、ZCode，以及其他能按协议读写本地文件的助手 | GPT 生成交接文字，用户粘贴发送；助手实现并写回报告，GPT 验收 |
| API | 已配置的 Responses API 模型，本机已测试 DeepSeek 官方 `deepseek-flash` | 独立 Codex CLI 执行器调用模型，收集改动，GPT 独立检查 |

Skill 不修改主 Codex 登录态或模型菜单。两条路线使用相同任务协议；登记助手模板不等于自动接通桌面软件。

## 安装

把本仓库链接交给 Codex，并说：

> 请安装这个仓库中的 skills/delegate-coding 技能，保留我已有的配置和修改。

也可以下载仓库，把整个 `skills/delegate-coding` 文件夹复制到自己的 Codex 技能目录。Windows 通常是 `%USERPROFILE%\.codex\skills`；设置了 `CODEX_HOME` 时使用其下的 `skills`。已有同名目录时先比较，勿直接覆盖。

需要 Python 3.11+ 和 Git。API 路线另需 Codex CLI、自己的厂商 API 密钥与可用额度。Windows 首次调用模型前需要显式配置本机沙箱实现，并通过不调用 API 的执行环境检查；由 GPT 协助操作即可。

## 首次配置后记住路径

首次使用 API 路线时，GPT 会定位随包脚本、Python、Codex CLI 和私有 API profile，核对后记录到这台电脑的本机安装副本。以后复用记录，无需每项任务都手输执行器的绝对路径。应用升级或文件移动后重新核对并更新记录。

共享版本只包含通用配置方法，不包含开发机的绝对路径、密钥或登录信息。更新已安装 Skill 时先保留自己的本机路径区段和其他修改。

## 使用

桌面路线示例：

> 使用“大脑控制小脑”，你负责方案和验收，走桌面路线，交给 WorkBuddy 实现。我的需求是……

API 路线示例：

> 使用 $delegate-coding，走 DeepSeek 官方 API 路线。先检查独立配置和本机执行环境，再做一个小任务验证。密钥只在本机输入，不要让我发到聊天。我的需求是……

## 已验证到哪一步

2026-10-16 的开发机实测：

- WorkBuddy：一次完整的领取、实现、报告、GPT 验收小任务通过，8 项测试通过；仍由用户复制发送交接文字。
- DeepSeek 官方 API：真实模型响应、命令工具、文件修改和正式报告均完成，GPT 独立运行 8 项测试通过；本机执行环境的项目内读写与项目外写入拒绝检查通过。
- API 实测由用户启动本机入口，随后自动执行，GPT 验收。当前隔离会话直接全自动发起尚未验证。
- ZCode：已有共享文件模板，尚未完成本机任务实测。Aider、MCP 自动派发和原生跨厂商子代理尚未集成。

这些结果验证了两个小任务，不代表所有设备、模型、长上下文或复杂项目都已验证。

## 文档

- [小白使用说明](docs/使用说明.md)
- [验证记录与限制](docs/验证记录.md)
- [共享任务协议](skills/delegate-coding/references/task-protocol.md)
- [桌面路线](skills/delegate-coding/references/desktop-route.md)
- [API 配置与执行环境检查](skills/delegate-coding/references/api-route.md)
- [运行路径配置与复用](skills/delegate-coding/references/runtime-paths.md)

离线测试：

```text
python -B -m unittest discover -s skills/delegate-coding/scripts -p "test_*.py" -v
```

## 分享内容

仓库只包含通用 Skill 源码和说明。每个使用者在自己的电脑配置 API；不共享开发机的密钥、加密凭据、本机 profile、登录文件、任务目录和运行日志。开发机专用验证入口没有打进本包。

仓库已有 `LICENSE` 时请保留；本包不替仓库所有者选择或更改许可证。
