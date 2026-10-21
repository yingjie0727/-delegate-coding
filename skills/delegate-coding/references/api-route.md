# API 路线：独立编码执行器

仅在用户选择 API 执行时读取。GPT 制定任务和验收，`api_worker.py` 启动独立 Codex CLI，让指定 Responses API 模型完成实现和本地工具循环。此路线不需要桌面助手登录态，也不会注册进主会话的模型菜单。

## 接口与配置

每个 API profile 使用独立的 `--worker-root`，并放在实现工作区之外。该目录保存非秘密配置、可选的 Windows 加密密钥和运行记录；不同厂商不要共用一个目录。

首次配置时由 GPT 按 [运行路径配置](runtime-paths.md) 定位随包脚本、Python 和 CLI，验证后记录在本机安装副本。后续复用记录，无需用户逐次提供绝对路径；GitHub 共享源码不包含开发机的个人路径。

```powershell
python scripts/api_worker.py --worker-root <API_ROOT> init --base-url https://api.deepseek.com --model <已确认的模型ID> --key-env DEEPSEEK_API_KEY --context-window <厂商真实窗口>
python scripts/api_worker.py --worker-root <API_ROOT> key-set
python scripts/api_worker.py --worker-root <API_ROOT> doctor
```

Windows profile 必须显式设置 `windows_sandbox`（`mxc`、`elevated` 或 `unelevated`），选用用户已配置且获准的实现。`init` 可加 `--windows-sandbox elevated`；这只是示例，不会关闭沙箱、自动选择较弱后端或复制主 Codex 配置。缺少此字段时，Windows 真实执行在领取任务前停止。

第一次调用模型前，以及更换 CLI、沙箱后端或权限设置后，先在实际执行任务的 Windows 用户终端运行不调用 API 的检查：

```text
python scripts/check_executor.py --worker-root <API_ROOT> --workspace <PROJECT> --codex <CODEX_EXE> --output-dir <NEW_CHECK_DIRECTORY>
```

检查以独立 profile 的同一 `codex-home`、受管权限和 `:workspace` 策略运行，验证项目内临时文件读写成功、项目外写入被拒绝；不读取密钥、不请求模型。失败就保留诊断并停止真实任务，不关闭沙箱来重试。`doctor` 的帮助和模型目录检查不能替代这个执行检查。旧版 Windows 执行器若只设置 `workspace-write` 而未选择本机沙箱实现，可能实际退回只读。

上面尖括号内容需要替换。`--codex <codex.exe或官方codex.js绝对路径>` 是全局选项，放在 `init`、`doctor`、`run` 之前。`init` 可加 `--provider-name "厂商显示名"`。初始化拒绝覆盖现有 `worker.json`，需要改配置时先读取并展示差异，再按本次授权修改。

`worker.json` 格式：

```json
{
  "schema_version": 1,
  "base_url": "https://responses.example.invalid/v1",
  "model": "example/code-model",
  "context_window": 128000,
  "key_env": "EXAMPLE_API_KEY",
  "provider_name": "Example Responses API"
}
```

`base_url` 是 HTTPS API 根地址，不能附加 `/responses` 或 `/chat/completions`，不能包含用户名、密码、查询参数或 fragment。`model` 和窗口必须由厂商文档或用户确认，示例不是可用服务。`key_env` 默认 `DEEPSEEK_API_KEY`，也可使用自定义环境变量；`provider_name` 仅用于显示。配置不存明文密钥。

## 密钥

Windows 用户在自己的本地交互终端执行 `key-set`，隐藏输入密钥；保存格式是当前 Windows 用户的 DPAPI 加密数据。不要让用户把密钥发到聊天、写进任务书、命令行参数或源码。`key-delete` 仅删除该 profile 的加密副本，不撤销厂商密钥，也不修改环境变量。

运行时优先读取 `key_env` 指定的环境变量，其次读取本 profile 的 `key.dpapi`。非 Windows 平台只支持环境变量；DPAPI 需要有效的当前用户配置，受限运行环境可能不可用。不要为了诊断擅自解密或展示已有凭证。

执行器使用该 profile 私有的 `codex-home` 作为独立 `CODEX_HOME`，用于保留合法初始化的 Windows 沙箱状态，同时避免把运行目录嵌在长任务路径下。每次 `codex exec` 都启动新任务，不恢复旧会话。同一 profile 串行检查和运行，避免并发改写配置。执行器移除继承的 Codex、ChatGPT、OpenAI 状态变量和名称包含 KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL 的变量，再仅注入本 profile 的凭证。配置还把选定的凭证变量从模型生成的 shell 环境中排除。这是凭证传递约束，不是完整的操作系统身份隔离；不要把含秘密的文件夹作为任务工作区。

## 执行一个任务

任务 JSON 复用通用字段 `title`、`goal`、`allowed_paths`、`constraints`、`acceptance`、可选 `context_files`。所有路径使用相对仓库根目录的 `/` 分隔形式：`src/` 表示整个目录，`src/main.py` 表示单个文件；`.` 表示全仓库，只有任务确实需要时才使用。

```powershell
python scripts/api_worker.py --worker-root <API_ROOT> run --workspace <干净Git工作区根目录> --task <task.json路径> --output-dir <工作区外的新输出目录> --timeout 900 --dry-run
```

先执行 `--dry-run` 检查配置和调用参数；它不会读取密钥或请求模型。确认路由、任务范围和用户已经授权使用该 API 后，再去掉 `--dry-run` 执行。真实运行会把任务和模型选择读取的项目内容发到所选厂商并可能计费。

执行前必须是干净 Git 工作区；优先使用专用 worktree。输出目录不能位于实现工作区中。运行有工作区锁、超时、Windows Job Object / POSIX 进程组清理；不允许递归委派。子 CLI 使用 `workspace-write`、不请求提权、禁用 hooks 和子代理，工具 shell 网络访问关闭。模型/API 通信仍可联网；需要网络的测试或依赖安装应由 GPT 在本次授权范围内另行安排。

结束后读取 `result.json`、`final.md`、`events.jsonl`、`changes.patch`、`git-status.txt` 和实际代码。未跟踪文件会列在结果中，其内容需要单独查看；不会自动进入 Git diff patch。返回值：

| 状态或退出码 | GPT 应如何处理 |
| --- | --- |
| `ready_for_review` / 0 | 工具循环正常结束，可以开始 GPT 验收；`accepted_by_gpt` 仍为 false |
| `review_required` / 3 | 检测到越界修改、暂存或提交，保留工作并先检查，不自动回滚 |
| `incomplete`、`failed` / 非零 | 检查真实事件和错误，不能声称完成 |
| `timed_out` / 124 | 执行器已尝试结束整个工作进程树；检查留下的修改再决定是否重试 |
| `interrupted` / 130 | 调用被中断，检查日志和改动 |
| 配置、前置条件错误 / 2 | 修复具体问题后重试 |

`allowed_paths` 是任务约束与执行后的检查，不是更细粒度的文件系统沙箱。GPT 必须审查改动；脚本不会自动提交、推送、合并、回滚或接受任务。

## 兼容范围与验证边界

只支持 Codex 能消费的 Responses 协议，包括它实际使用的流式事件和工具调用。普通 Chat Completions 兼容、`/models` 列表正常或单次文本成功，都不足以证明可用。脚本不提供 Chat Completions 转换、不做 ChatGPT OAuth 转发，也不保证任意标注 Responses 的服务已通过验证。

当前生成的模型目录声明文本输入、`unified_exec` shell、freeform `apply_patch` 和 high 推理等级。新厂商接入前要验证这些工具与参数；不兼容就报告明确原因并调整适配，不静默假装支持。上下文填写不会扩大模型真实能力，压缩也需真实多轮任务验证。

`doctor` 只执行离线 `--version`、`exec --help` 和 `debug models`，检查配置、Git、凭证是否可获得以及 CLI 本地目录兼容性；`ready_for_live_test` 仅表示可以尝试真实测试。离线单元测试使用假 CLI 和假密钥，覆盖隔离、日志脱敏、范围检查、失败与超时，不证明厂商 API 可用。

2026-10-16，开发机通过 `https://api.deepseek.com` 和 `deepseek-flash` 完成了一次真实编码小任务：模型调用工具修改指定 Python 文件，GPT 独立运行 8 项测试全部通过并正式接受任务。独立 Windows 执行器的项目内读写和项目外写入拒绝检查也已通过。其他厂商、设备、长上下文和多轮复杂任务仍需各自验证。默认 128000 是执行预算，不是宣称厂商窗口上限。

本次实测由用户双击本机入口启动，随后自动执行，GPT 读取结果并验收。开发机 Codex 命令沙箱与用户终端是不同 Windows 身份，尚未验证当前沙箱直接读取用户 DPAPI 凭据并全自动发起。分享包不包含开发机的密钥、profile、运行日志或写死个人路径的验证入口；使用者需在自己的电脑初始化配置并验证启动方式。Aider、MCP 自动派发和原生跨厂商子代理不在本包已验证范围内。

相关官方资料：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[CODEX_HOME 和 env_key](https://learn.chatgpt.com/docs/config-file/environment-variables)、[DeepSeek Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api/)、[DeepSeek 模型资料](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。端点、模型 ID 和能力应在真实启用前核对厂商当前文档。
