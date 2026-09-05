# 小智 GRBL 工具边界

本仓库是小智/MOSS 的云端 MCP 扩展，不是 ESP32 固件侧 `self.grbl.*` 工具实现。小智设备负责语音入口、唤醒、ASR/TTS、显示和音频会话；本仓库通过 `moss_mcp/bridge.py` 和 `moss_mcp/server.py` 暴露 Python MCP 工具，再路由到 `tools/*.py`。

使用者入口见 [用户快速入口](../user/quick-start.md)，维护者改高风险工具前先看 [开发者安全入口](../developer/safety.md)。

## 边界原则

- `self.grbl.*` 名称属于小智设备固件侧能力，可能直接控制设备本地 UART；不要把它们和本仓库的 Python MCP 工具混用。
- 本仓库的激光动作必须保留代码安全门禁：危险单命令需要 `confirmed=true`，完整任务走 `send_file`、`start_tuned_job_tool` 或 `ai_laser_gcode_tool` 的 `send_generated` 委托路径。确认后的完整文件发送会先做只读在线探测再流式发送。
- `laser_safe_action_tool` 只是语义别名层，把自然动作映射到现有串口或网络单命令路径；它不绕过底层命令分类和确认规则。
- `check_laser_connection_tool` 只做连接检查。默认串口用 `?` 探测，网络默认 Telnet `?`，设置 `transport="http"` 时才使用 HTTP `[ESP800]`。
- `ai_laser_gcode_tool` 和文字/图片生成工具只负责准备文件、预览、summary 或发送委托；`generate`、`inspect`、`image_search`、`analyze_image`、`test_ai_provider` 都不启动机器，`send_generated` 仍要复用底层发送门禁。

## 路由建议

| 用户意图 | 推荐工具 | 关键边界 |
| --- | --- | --- |
| “检查激光机开没开、连没连上” | `check_laser_connection_tool` | 只读探测，默认摘要很短；排障时再用 `include_detail=true` |
| “查状态、解锁、回零、设原点、关激光、移动、急停、软复位” | `laser_safe_action_tool` | `status` 可只读；其他状态改变动作必须确认 |
| “帮我刻 xxx、刻名字、刻一段文字” | `laser_workflow_tool` `action="preview"`, `source_type="text"` | `xxx` 是文字内容，不是文件名；底层可生成文字任务，默认不发送机器 |
| “文字效果太焦/太浅/切不透/毛边/断线” | `refine_laser_params_from_feedback_tool`，必要时 `regenerate_text_laser_task_tool` | 先建议或重生成文件，不直接控制硬件 |
| “把这张图/候选图/图片 URL 转成 G-code 或 NC” | `ai_laser_gcode_tool` | `generate` 生成最终文件前要求材料和厚度；`inspect`/`image_search`/`analyze_image` 不启动机器；`send_generated` 仍需确认 |
| “把文字做成 G-code 文件” | `generate_text_image_gcode_tool` 或 `generate_text_laser_task_tool` | 前者只生成文件；后者带任务记录、材料参数和反馈闭环 |
| “先调参、打测试矩阵、找参数” | `run_calibration_grid_tool`，再用 `select_calibration_cell_tool` | 生成矩阵默认不发送；确认后才运行 |
| “开始正式雕刻/切割这个任务” | `laser_workflow_tool` `action="confirm_send"`；调参正式任务可用 `start_tuned_job_tool` | 第一次用 `confirmed=false` 或 `dry_run=true` 预览参数；已有 `workflow_id` 时继续同一 workflow |
| “发送已有 G-code/图片/default 文件” | `laser_grbl_tool` 或 `laser_network_grbl_tool` 的 `send_file` | 仅用于明确已有文件；`confirmed=true` 后才探测设备并发送 |
| “发送 $$ / $I / ? 之类单条命令” | `laser_grbl_tool` 或 `laser_network_grbl_tool` 的 `send_command` | 单命令不是完整任务路径；运动/激光/配置写入需要确认 |

## 禁止绕行

不要使用 `command_execution_tool`、固件侧直接控制假设、HTTP 逐行发送完整任务，或用户历史上下文中猜测的 IP/串口/文件名来绕过激光安全检查。网络完整文件发送在当前代码中强制走 Telnet；`laser_network_grbl_tool` 的单条 `send_command` 默认 `transport="http"`，但 HTTP 只适合只读查询或单条受控命令。
