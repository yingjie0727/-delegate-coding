# WorkBuddy 桌面操作与回执

使用已获授权的本机桌面操作工具，将 `HANDOFF.txt` 发送到 WorkBuddy。桌面操作须能发现并操作目标窗口。`desktop_bridge.py` 负责记录投递，界面操作由当前会话完成。

## 发送和核对

1. 按共享协议创建 `desktop` / `workbuddy` 任务。准备发送，读取返回的 `handoff_text`。

   ```text
   python <bridge> --board <board> prepare --task-id <id>
   ```

2. 重新定位并前置 WorkBuddy，确认程序、空白任务页、工作目录和没有待发草稿。输入前登记目标，保存返回的 `attempt_id`。

   ```text
   python <bridge> --board <board> begin --task-id <id> --app <app> --window <observed-window> --conversation <conversation> --evidence <observed-target>
   ```

3. 通过原生文本输入填入 `handoff_text`，读取输入框核对完整内容。若粘贴后输入框仍为空，可重新定位输入框并改用原生文本输入。点击已启用的发送按钮一次。
4. 在新会话正文中看到对应任务编号和已发文字后，登记发送证据。

   ```text
   python <bridge> --board <board> record --task-id <id> --attempt-id <attempt> --outcome sent_verified --evidence <observed-message>
   python <bridge> --board <board> status --task-id <id>
   ```

5. 等 WorkBuddy 领取并提交报告，从任务目录读取结果并独立验收。`sent_verified` 表示已发送，领取和验收分别看 `worker_acknowledged` 和 `accepted_by_gpt`。

`<bridge>` 为随包 `scripts/desktop_bridge.py` 的绝对路径。

## 连接中断或找不到窗口

- 找窗口时核对调用环境。Windows 命令进程可见 WorkBuddy 进程、却返回 0 个主窗口时，先检查命令是否运行在隔离桌面。通过可访问用户桌面的已授权操作工具重新定位。
- 将发送中断记录为 `uncertain`。查询原会话是否已有任务消息，再对照任务板的领取记录。领取超时本身不足以判定没有发送。
- 核实前一次确实没有发送、输入框中也没有本次草稿后，用 `release-unsent --confirmed-unsent` 保存证据并解除旧尝试。随后重新核对目标并登记新的尝试。

  ```text
  python <bridge> --board <board> release-unsent --task-id <id> --attempt-id <attempt> --confirmed-unsent --evidence <observed-unsent>
  ```

2026-11-21 已在 WorkBuddy 5.11.3 验证文件往返。第一次结果为输入哈希，明确要求逐字符复制后，复测通过。验收时检查实际文件内容与任务编号。
