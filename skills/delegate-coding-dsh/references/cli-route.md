# CLI 路线：自动派发给 CodeBuddy CLI

仅在用户选择 CLI 执行时读取。这条路线的执行器是 CodeBuddy CLI（WorkBuddy 桌面版同一引擎的命令行形态），由协议脚本直接启动，**不需要用户复制粘贴**。

## 与桌面路线的分工

| 项目 | desktop | cli |
| --- | --- | --- |
| 派发 | 用户复制 `HANDOFF.txt` 发送 | 协议脚本启动进程 |
| 交接文件 | 相同 | 相同（TASK.md / controller.py / 报告） |
| 领取与报告 | 执行器自己跑 claim/report | 执行器自己跑 claim/report |
| 人工介入 | 每次任务 | 只在验收和异常时 |

两条路线共用任务板、工作区锁、报告 schema 和 review 语义。CLI 路线只是把"人把文字发过去"换成"进程被拉起来"。

## 前置条件

1. 安装 CodeBuddy CLI：`npm install -g @tencent-ai/codebuddy-code`，并用 `codebuddy --version` 确认可执行。
2. 先交互式登录一次（`codebuddy`，国内版微信扫码 / 国际版 GitHub 授权）。登录态属于本机用户，不由本 skill 代管。
3. Python 3.11+ 与 Git。工作区必须是**干净**的 Git 仓库，CLI 路线不接受 `--allow-no-git`。

## 创建任务

```text
python <script> --board <board> create --route cli --executor codebuddy \
  --workspace <clean-project> --task <spec.json> [--cli-path <verified-executable>] [--model <id>]
```

- `--cli-path` 省略时按 PATH 和常见 npm 全局目录查找 `codebuddy`，找不到就报错，不会静默失败。
- 创建时把可执行文件绝对路径（以及可选的 `--model`）写进 `task.json` 的 `cli` 字段。**之后换执行器或模型必须新建任务**，不能在一次任务里偷偷换。
- 任务目录、TASK.md、HANDOFF.txt、controller.py 与桌面路线一致；领取和报告仍由执行器自己完成。

## 派发

```text
python <script> --board <board> run-cli --task-id <id> [--dry-run] [--timeout 900] [--max-turns N]
```

1. `--dry-run` 只做离线检查：解析可执行文件、跑 `--version`、打印将要使用的 argv，不启动任务。
2. 真实执行会把 `HANDOFF.txt` 内容作为 `-p` 参数传给 CLI，附加 `-y`（跳过交互确认）与绑定过的 `--model`，工作目录固定为任务工作区。
3. 标准输出与错误输出保存在任务目录的 `cli-output/`，运行摘要保存在 `cli-run.json`。它们可能包含项目内容，**不要当作公开日志**。
4. 超时（默认 900 秒）会终止整个进程树，并把任务记为失败；留下的改动不自动回滚。

## 退出码

| 退出码 | 含义 | 处理 |
| --- | --- | --- |
| `0` | 执行器提交了 `completed` 报告，进入 `ready_for_review` | 仍需独立复核 diff 与测试 |
| `3` | 执行器报告 `blocked` 或 `failed` | 读报告，决定修复还是换任务 |
| `2` | 没有可验证报告（未领取、未提交、启动失败） | 协议脚本已代记失败报告，检查真实文件 |
| `124` | 超时并已终止进程树 | 检查残留改动后再决定是否重试 |

退出码为 `0` **不代表验收通过**。它只表示执行器提交了报告，`review` 仍由大脑决定。

## 安全边界

- `-y` 让 CLI 跳过交互确认，因此它能做的操作等同于当前用户在该工作区能做的事。`allowed_paths` 是任务约束与事后检查，**不是文件系统沙箱**。
- 协议在 `collect` 时会用 Git 对比真实改动、未跟踪文件、暂存区与 HEAD；越界改动会让 `review --decision accept` 直接失败。
- 不要让 CLI 路线执行需要联网安装依赖、读取凭据或改系统配置的任务。此类任务应先由大脑单独安排。
- 执行器的登录态、额度和计费属于它自己的账号，本 skill 不读取也不转发。

## 验证范围

截至本版，CLI 路线的自动派发**只用离线假 CLI 验证过**：领取、实现、报告、未报告、超时、可执行文件绑定与越界改动检查都有测试覆盖。真实 CodeBuddy CLI 的参数（`-p`、`-y`、`--model`、`--max-turns`）来自其官方文档，但尚未在本机实际跑过一次完整任务。首次使用请先派发一个极小任务，确认 argv 与退出码符合预期。
