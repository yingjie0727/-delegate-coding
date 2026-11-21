# 第三条路线　CodeBuddy CLI

选用这条路线时，GPT 规划任务和验收，腾讯 CodeBuddy Code 在命令行执行。使用已安装并登录的 `codebuddy`（别名 `cbc`）以及 Python 3.11+ 和 Git。测试工作区使用独立、干净的 Git 根目录，任务板放在工作区外。

## 确认、派发、收回

1. 在实际运行任务的环境中执行 `codebuddy --version`。首次登录在 CodeBuddy 自己的交互会话完成。先在独立测试目录运行一个短文本探针。

   ```powershell
   'Reply with exactly CB-CLI-OK' | codebuddy -p --input-format text --output-format text --tools= --strict-mcp-config --no-session-persistence --max-turns 1
   ```

2. 准备一项边界清楚的小任务和 `spec.json`。固定执行器及工作区，按需用 `--cli-path` 绑定已核对的可执行文件，或用 `--model` 指定已核对的模型 ID。

   ```text
   python <controller> --board <board> init
   python <controller> --board <board> create --route cli --executor codebuddy --workspace <git-root> --task <spec.json>
   python <controller> --board <board> run-cli --task-id <id> --dry-run
   python <controller> --board <board> run-cli --task-id <id> --timeout 900
   ```

3. 任务通过标准输入传给 `-p` 会话。使用 `acceptEdits` 与明确的文件工具列表 `Read,Edit,Write,Glob,Grep`。任务领取和报告提交由控制脚本完成，测试命令由 GPT 在验收时运行。`allowed_paths` 用于约束任务和复核 Git 改动。
4. 查看状态和收取结果。核对报告、实际差异、未追踪文件和测试，再记录验收。

   ```text
   python <controller> --board <board> status --task-id <id> --plain --tail 0
   python <controller> --board <board> collect --task-id <id>
   python <controller> --board <board> review --task-id <id> --decision accept --note "<actual acceptance evidence>"
   ```

`<controller>` 为随包 `scripts/delegate.py` 的绝对路径。发送给 CodeBuddy 的任务和读取的项目内容应限于用户授权范围。输出日志留在对应任务目录的 `cli-output/`，运行摘要见 `cli-run.json`。

## 失败怎么查

| 现象 | 怎么处理 |
| --- | --- |
| CLI 输出 `Authentication required`，进程退出码仍是 `0` | 检查运行任务的环境下 CodeBuddy 的登录态。仅有退出码而无有效报告的任务会记为失败 |
| CLI 无法连接代理或模型服务 | 用文本探针检查同一环境。提示 `502` 或 `ECONNREFUSED` 时，核对配置的代理地址和监听状态 |
| 输出 `blocked` 或工具拒绝 | 读取具体错误，检查当前工作区和工具范围。任务需要额外工具时由 GPT 承担相应步骤或按用户选择重新安排 |
| 文件改动和报告不一致 | 收集实际差异。超出任务范围、HEAD 改动、暂存改动或报告与实际文件不匹配时，本次会记为失败 |
| 超时 | 运行器终止子进程并保留已有改动，检查当前工作区后再安排后续任务 |

运行器退出码 `0` 表示进入待验收。`3` 表示执行器报告阻塞、失败或任务收到取消请求。`2` 表示启动、进程、报告或改动验证失败。`124` 表示超时。

参见 [腾讯 CodeBuddy 无头模式](https://www.codebuddy.cn/docs/cli/headless)和 [CLI 参考](https://www.codebuddy.cn/docs/cli/reference)。使用 `codebuddy --help` 核对本机版本支持的选项。
