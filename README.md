# 大脑控制小脑

一个 Codex Skill：GPT 负责规划、拆任务和验收，将范围明确的代码实现交给外部执行器。

显示名称：**大脑控制小脑**。内部调用名：`$delegate-coding`。

更新于 2026-11-21。

## 三条路线

| 路线 | 执行器 | 使用方式 |
| --- | --- | --- |
| 桌面助手 | WorkBuddy、ZCode 等 | WorkBuddy 可通过已授权的桌面操作发送。使用共享任务文件交接，GPT 验收 |
| API | 已配置的 Responses API 模型，本机已测试 DeepSeek 官方 `deepseek-flash` | 独立 Codex CLI 执行器调用模型，收集改动，GPT 独立检查 |
| CLI | 腾讯 CodeBuddy Code（`codebuddy` 或 `cbc`） | 从干净的 Git 工作区启动无头 CLI，收集结果并核对实际改动 |

三条路线共用任务板、范围约束和验收。每项任务固定使用选定的执行器。

## 安装

把本仓库链接交给 Codex，并说：

> 请安装这个仓库中的 skills/delegate-coding 技能，保留我已有的配置和修改。

也可以下载仓库，把整个 `skills/delegate-coding` 文件夹复制到自己的 Codex 技能目录。Windows 通常是 `%USERPROFILE%\.codex\skills`；设置了 `CODEX_HOME` 时使用其下的 `skills`。已有同名目录时先比较，勿直接覆盖。

需要 Python 3.11+ 和 Git。API 路线另需 Codex CLI 和已配置的厂商 API。CLI 路线需在同一运行环境中可以启动并使用已登录的 CodeBuddy Code。

## 首次配置后记住路径

首次使用 API 路线时，GPT 会定位随包脚本、Python、Codex CLI 和私有 API profile，核对后记录到这台电脑的本机安装副本。以后复用记录，无需每项任务都手输执行器的绝对路径。应用升级或文件移动后重新核对并更新记录。

共享版本只包含通用配置方法，不包含开发机的绝对路径、密钥或登录信息。更新已安装 Skill 时先保留自己的本机路径区段和其他修改。

## 使用

桌面路线示例：

> 使用“大脑控制小脑”，你负责方案和验收，走桌面路线，交给 WorkBuddy 实现。我的需求是……

API 路线示例：

> 使用 $delegate-coding，走 DeepSeek 官方 API 路线。先检查独立配置和本机执行环境，再做一个小任务验证。密钥只在本机输入，不要让我发到聊天。我的需求是……

CLI 路线示例：

> 使用 $delegate-coding，走 CodeBuddy CLI 路线。请先核对 CLI 版本，再派一个能独立验收的小任务。

## 已验证到哪一步

- **WorkBuddy 桌面操作**于 2026-11-21 完成发送、领取、原样回写和 GPT 验收。测试使用了 WorkBuddy 5.11.3 和独立的本地文件任务。
- **CodeBuddy CLI 2.185.0** 于 2026-11-21 通过非交互派发、文件原样回传、结构化报告和 Git 验收。
- **DeepSeek 官方 API** 于 2026-10-16 从本机入口完成真实模型响应、命令工具、文件修改和报告，GPT 独立运行 8 项测试通过。
- **ZCode** 已有共享文件交接模板，本机任务待验证。

[验证记录](docs/验证记录.md)记载了各次实测的范围和故障原因。

## 文档

- [小白使用说明](docs/使用说明.md)
- [验证记录与限制](docs/验证记录.md)
- [共享任务协议](skills/delegate-coding/references/task-protocol.md)
- [桌面路线](skills/delegate-coding/references/desktop-route.md)
- [WorkBuddy 桌面操作与回执](skills/delegate-coding/references/desktop-control.md)
- [API 配置与执行环境检查](skills/delegate-coding/references/api-route.md)
- [CodeBuddy CLI 第三条路线](skills/delegate-coding/references/cli-route.md)
- [运行路径配置与复用](skills/delegate-coding/references/runtime-paths.md)

离线测试：

```text
python -B -m unittest discover -s skills/delegate-coding/scripts -p "test_*.py" -v
```

## 分享内容

仓库只包含通用 Skill 源码和说明。每个使用者在自己的电脑配置 API；不共享开发机的密钥、加密凭据、本机 profile、登录文件、任务目录和运行日志。开发机专用验证入口没有打进本包。

仓库已有 `LICENSE` 时请保留；本包不替仓库所有者选择或更改许可证。
