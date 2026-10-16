# 大脑控制小脑

一个 Codex skill：GPT 负责规划、拆任务和验收，把范围明确的实现交给另一个执行器。

内部名称为 `delegate-coding`，安装后的显示名称为“大脑控制小脑”。

## 两条执行路线

| 路线 | 执行器 | 当前交接方式 |
|---|---|---|
| 桌面助手 | WorkBuddy、ZCode，或注册的其他助手 | 用户把 GPT 生成的交接文字粘贴到助手；助手修改共享项目并写回结果；用户通知 GPT 验收 |
| API | 独立配置的 Responses-compatible 模型 | GPT 调用独立 Codex CLI 执行器，收集实际修改，再检查验收 |

桌面账号和 API 密钥各自独立。增加桌面助手可以复用同一任务协议；登记模板不代表已经完成真实接入验证。

## 安装

可以把本仓库链接交给 Codex，并说：

> 请从这个 GitHub 仓库安装 skills/delegate-coding 技能，保留我已有的配置。

也可以下载仓库，把整个 `skills/delegate-coding` 文件夹复制到自己的 Codex 个人技能目录。Windows 默认是 `%USERPROFILE%\.codex\skills`；设置了 CODEX_HOME 时使用其下的 skills 目录。已有同名技能时先比较内容，保留自己的修改。

开始使用前需要 Python 3.11+、Git。API 路线另需 Codex CLI、有效的厂商 API 密钥，以及真实的流式/工具调用兼容验证。

## 使用

直接告诉 GPT：

> 使用“大脑控制小脑”，你负责方案和验收，走桌面路线，交给 WorkBuddy 实现。我的需求是……

或使用内部调用名：

> 使用 $delegate-coding，走 API 路线。先检查配置并验证 DeepSeek 接口，再执行我的任务。

首次 API 配置需要用户在本机隐藏输入密钥，不要把密钥发送到聊天或提交到仓库。

## 说明

- [小白使用说明](docs/使用说明.md)
- [共享任务协议](skills/delegate-coding/references/task-protocol.md)
- [桌面助手接入](skills/delegate-coding/references/desktop-route.md)
- [API 执行器配置](skills/delegate-coding/references/api-route.md)
- [验证记录](docs/验证记录.md)

同一 checkout 始终复用同一个 board，一次只让一个执行器修改。模型的“完成”需要 GPT 检查实际代码与测试；范围规则属于任务约束和事后检查，不是操作系统权限隔离。

## 验证状态

51 项离线测试中 50 项通过，1 项 DPAPI 测试因受限 Windows 用户配置跳过。没有做真实 API 调用。WorkBuddy 只验证过基本文件往返，新版完整协议与 ZCode 接入仍需用户在本机做小任务实测。

运行本地离线测试：

```text
python -B -m unittest discover -s skills/delegate-coding/scripts -p "test_*.py" -v
```

## 分享和许可

仓库所有者在 GitHub 选择并添加 LICENSE，说明允许他人如何使用、修改和分发。分享包没有代替作者决定授权条款。使用时查看仓库的 LICENSE。

仓库只放 skill 源码与说明；API 密钥、加密密钥文件、本机 API 配置、Codex 登录文件、任务书和运行日志留在本机。
