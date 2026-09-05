# 激光文档索引

本目录收集本仓库 MCP 激光控制相关的人类可读文档。源码事实以 `moss_mcp/server.py`、`tools/` 下的激光相关工具、`core/ai_laser_gcode/` 和对应测试为准；文档用于说明路由、安全边界和维护约束。

## 固件与硬件基线

当前激光链路按 Grbl_ESP32 / ESP3D WebUI 风格控制器理解，命令参考来自外部 `Grbl_Esp32-master` 源码。除非有只读实机证据证明不一致，否则网络控制按该命令体系处理。

硬件默认基线是大鱼 DIY 翼宿 V1.0：`100 mm x 100 mm` 行程、G3 主板、`24 V 5 W` 激光头、`24 V 6 A` 电源。生成 G-code、测试矩阵和安全边界默认按该机器处理。

## 当前 MCP 激光工具面

`moss_mcp/server.py` 会优先注册连接检查、语义安全动作、调参、文字任务、图片转 G-code、串口发送和网络发送等主链路模块；其他 `tools/*.py` 继续按文件名自动发现。当前 `DISABLED_TOOLS` 只默认禁用 `tools.homeasstant_tool` 和 `tools.lasergrbl_gui_tool`，摄像头工具仍受 `ENABLED_IP_CAMERA=true` 控制。当前默认可注册的激光相关 MCP 工具包括：

| 工具 | 主要用途 | 是否默认启动机器 |
| --- | --- | --- |
| `check_laser_connection_tool` | 用只读探测检查串口/网络是否连通 | 否 |
| `laser_safe_action_tool` | 语义化单动作，如 `status`、`unlock`、`home`、`set_origin`、`laser_off`、移动、急停、软复位 | 除 `status` 外需要确认 |
| `laser_workflow_tool` | 小智、Web 和 Android 上层入口共享的高层工作流：预览、确认发送、状态、取消、反馈、重生成 | 预览/状态/反馈不启动机器，`confirm_send` 仍需确认 |
| `laser_grbl_tool` | 串口 GRBL：机型信息、图片转 G-code、完整文件发送、单命令、后台任务 | `send_file` 需要 `confirmed=true` |
| `laser_network_grbl_tool` | Grbl_ESP32/ESP3D 网络：HTTP/Telnet 单命令、完整文件发送、后台任务 | 完整文件强制 Telnet 且需要 `confirmed=true` |
| `material_params_tool` / `recommend_laser_params_tool` | 材料参数库和推荐参数 | 否 |
| `run_calibration_grid_tool` / `select_calibration_cell_tool` | 调参测试矩阵和最佳格保存 | 生成默认不发送，发送需要确认 |
| `start_tuned_job_tool` | 使用材料推荐、调参结果或文字任务启动正式任务 | 首次默认只预览，确认后才发送 |
| `generate_text_image_tool` | 生成纯白底黑字文字素材图片 | 否 |
| `generate_text_image_gcode_tool` | 一步生成文字图片并转 G-code；文字 outline 雕刻和文字切割优先走 glyph/fontTools 字体矢量轮廓 | 否 |
| `generate_text_laser_task_tool` / `refine_laser_params_from_feedback_tool` / `regenerate_text_laser_task_tool` | 文字激光任务、反馈调参、重新生成 | 默认不发送 |
| `ai_laser_gcode_tool` | 统一图片/文字素材、候选图、预览、summary、G-code/NC 生成与已生成文件发送委托 | 生成不发送，`send_generated` 仍走确认门禁 |

`moss_mcp/web_server.py` 是本机浏览器输入入口，不是新的底层激光发送器。它把“手动输入文字、生成预览、点击确认网络发送”包装成网页流程；生成和发送阶段都通过 `laser_workflow_tool` 共享同一个 `workflow_id`，确认发送仍委托现有调参任务和网络完整任务门禁。

`android/laser-native-app` 的 Tasks 原生页只负责文字 workflow 的 `preview` / `status`、summary、next_actions、原始 JSON 和双预览图展示。它不使用 WebView，不确认发送、不取消/反馈/重生成、不直连设备、不在 Android 本地生成 G-code。

文字 workflow、Web 页和 Android 预览默认可先复用已有 G-code：`reuse_prepared_gcode=true` 时，`generate_text_laser_task_tool` 会在 `LASER_PREPARED_GCODE_DIR` 中查找 `.gcode` / `.nc` 文件，先按文字内容精确匹配文件名，再做包含匹配；唯一命中进入 `prepared_gcode` 预览，多命中时返回候选让用户选择。该路径仍不直接启动机器，确认发送继续走 `confirm_send` 和底层发送门禁。

## 文档

- [用户快速入口](../user/quick-start.md): 最短启动、小智/MOSS 连接和默认安全激光流程。
- [开发者安全入口](../developer/safety.md): 工具注册现实、高风险能力、确认门和 mock-only 测试要求。
- [Laser Hardware BOM Baseline](./hardware-bom.md): 机器硬件基线、行程、功率刻度和软件影响。
- [Grbl_ESP32 网络控制命令参考](./grbl-esp32-network-commands.md): Grbl_ESP32 / ESP3D WebUI 网络控制命令、HTTP/WebUI 入口、Telnet/GRBL 查询、危险命令和自动化策略。
- [Xiaozhi GRBL Tool Boundary](./xiaozhi-grbl-tool-boundary.md): 小智固件侧 `self.grbl.*` 与本仓库云端 MCP 激光工具的边界。
- [Xiaozhi/MOSS Prompt Archive](../integration/xiaozhi-prompts/latest.md): 可贴到小智/MOSS 控制台的最新版提示词和知识库文本（控制台 ≤2000、知识库 ≤200 总字符，空格计入），历史版本保存在 `docs/integration/xiaozhi-prompts/history/`，不能替代代码门禁。
- [语音图片转 G-code/NC 引擎](./voice-image-gcode-engine.md): `ai_laser_gcode_tool` 的素材解析、候选选择、summary 契约、播报、首版 AI 生图禁用和发送边界。

## 安全规则

激光控制会影响真实硬件。文档示例只能使用 `<device-ip>`、`<telnet-port>`、`<path>` 等占位符，不要把真实设备 IP、串口号、Wi-Fi 密码、WebUI 密码、Token 或用户私有路径写入提交内容。

生成图片、生成 G-code、生成文字任务、分析图片、推荐参数和反馈调参都不等于启动机器。完整文件发送、运动、开激光、切割、GRBL 设置修改和危险实时命令必须经过代码中的 `confirmed` 门禁。完整文件发送在确认后还会先做只读在线探测：串口用 `?`，网络完整文件强制 Telnet 并用 `?`；单条危险命令依赖命令分类和 `confirmed` 门禁，不能代替完整任务发送路径。

`laser_workflow_tool cancel` 的含义是按已绑定的 sender `job_id` 停止继续发送排队中的 G-code；它不能承诺控制器已经物理急停。用户明确说“急停、暂停机器、关激光”时，仍应走 `laser_safe_action_tool` 的语义安全动作。

## 材料参数维护规则

本机实测材料参数的唯一维护入口是 `.lasergrbl_materials.json`，或 `.env` 中 `LASER_MATERIAL_PARAMS_FILE` 指向的私有文件。`tools/laser_material_calibration_tool.py` 里的默认材料参数只作为内置兜底；`.lasergrbl_calibrations/` 只保留测试矩阵历史，不作为手动维护的推荐参数来源。完成测试矩阵后，用户说“第 N 格最好”时使用 `select_calibration_cell_tool` 将该格参数写入材料库。
