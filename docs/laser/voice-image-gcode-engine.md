# 语音图片转 G-code/NC 引擎

本文说明小智/MOSS 通过统一 MCP 工具把图片、文字或候选素材准备成激光 G-code/NC 文件的流程。该能力只负责生成文件、预览和摘要；生成成功不等于启动机器。

## 概览

统一入口是 `ai_laser_gcode_tool`，通过 `action` 区分行为：

| action | 作用 | 是否可触发真实设备 |
| --- | --- | --- |
| `generate` | 生成最终 G-code/NC、处理后预览、加工路径预览和 summary | 否 |
| `inspect` | 只分析图片并写检查 summary，不生成可发送生产文件 | 否 |
| `image_search` | 保存图片候选并要求用户二次选择 | 否 |
| `analyze_image` | 分析图片适合 `outline` 还是 `raster`，建议仅作参考 | 否 |
| `test_ai_provider` | 检查 AI 分析配置是否完整 | 否 |
| `send_generated` | 读取 summary 后交给现有串口/网络发送门禁 | 只有确认门禁通过后才可能 |

`generate` 写最终文件前必须有明确材料和厚度。缺材料或厚度时，工具返回追问和 `speech`，不会调用生成引擎写最终 G-code/NC。

`inspect`、`image_search`、`analyze_image` 和 `test_ai_provider` 不要求材料和厚度即可运行，因为它们只做检查、候选保存、AI 分析配置检查或建议生成，不产出可直接发送的生产文件。

`moss_mcp/server.py` 会自动发现并注册 `tools/laser_asset_gcode_tool.py`，当前该工具默认可用；`lasergrbl_gui_tool` 仍在禁用列表中，不属于该语音图片/G-code 主路径。

## 语音素材来源

语音没有 GUI 文件选择器，因此素材来源按固定优先级解析：

1. 用户明确提供的本地图片路径、图片 URL 或文字内容。
2. 工具记录的最近素材，例如刚生成的文字图片或刚选择的候选图片。
3. 固定输入目录中的最新图片，默认是 `laser_inputs/`，可用 `AI_LASER_INPUT_DIR` 覆盖。
4. 仍无法确定时返回追问，不猜文件名、不编造路径。

运行状态默认保存在 `.ai_laser_gcode_state/state.json`，可用 `AI_LASER_GCODE_STATE_PATH` 覆盖。状态只保存素材路径、URL 和候选元数据，不应保存 API Key、token、密码或设备地址。

文字和网络图片素材会先落到 `AI_LASER_GCODE_ASSETS_DIR`，默认是 `generated_assets/`。图片搜索候选默认有效期是 `AI_LASER_CANDIDATE_TTL_SECONDS=1800` 秒，过期、缺失或序号越界时工具会追问，不会生成最终文件。

## 生成模式

`mode` 支持三类语义：

| mode | 说明 |
| --- | --- |
| `outline` | 适合图标、文字、线稿、logo 等轮廓雕刻或切割准备。 |
| `raster` | 适合照片或灰度层次明显的图片，会生成光栅加工路径。 |
| `auto` | 由本地图像分析决定单一推荐，置信度不足时返回候选，不自动生成最终文件。 |

首版 AI 分析只能辅助说明图片特征，不能替代材料库、安全检查、预览确认或用户确认。

本地生成只接受 `output_format="gcode"` 或 `output_format="nc"`。安全校验还会限制 `mode`、尺寸、功率、速度、次数、像素间距、overscan、矢量简化、填充间距和光栅输出策略等范围；超出范围会失败而不是生成可发送文件。

## 图片搜索二阶段

`image_search` 只保存候选并返回候选列表。它不会自动选择第一张，也不会生成最终文件。

后续用户必须说“选第 N 张”、提供候选 ID，或明确给出图片 URL，工具才会把该候选作为素材进入 `generate`。候选上下文缺失、过期或序号越界时，工具返回追问，不调用生成引擎。

## 预览与预计时间播报

成功生成或检查后，返回 payload 会包含适合小智直接播报的 `speech` 文本。播报应简短说明：

- 处理后预览是否生成。
- 加工路径预览是否生成。
- 预计加工时间。
- 模式、材料、厚度、功率、速度和输出格式。
- 下一步建议，例如查看预览、补充参数、选择候选或确认发送。

本地完整路径、图片 URL、API Key、token、密码和设备地址不放进播报文本；这些信息只保留在结构化字段中供工具链使用。

`speech` 会对 URL、本地绝对路径和常见密钥字段做脱敏。结构化字段仍可能包含本地路径，供 MCP 工具链继续读取文件。

## Summary 安全契约

生成引擎写出的 `summary.json` 是发送阶段的稳定契约，当前 `contract_version` 为 `1`。发送前至少检查：

- `contract_version == 1`。
- `gcode_path` 存在于 summary 中。
- `recommendation_status == "single_recommendation"`。
- `can_send == true`。
- `safety_report.can_send` 不是 `false`。

任一条件不满足时，`send_generated` 返回 `send_status="blocked"` 和中文播报原因，不委托串口或网络发送。

即使 `summary` 可发送，`confirmed=false` 且 `dry_run=false` 时也只返回 `send_status="confirmation_required"`，不会调用底层发送。传入 `summary_path` 且未提供新素材时，工具会把 `action="generate"` 自动视为 `send_generated`，但仍执行同一套 summary 校验和确认门禁。

## 发送确认门禁

生成文件、检查图片、搜索候选和分析图片都不连接真实设备。

只有 `send_generated` 会读取已生成 summary 并进入发送流程。即使 summary 可发送，`confirmed=false` 时也只返回确认提示，不会调用串口或网络发送。用户明确确认后，真实发送仍复用现有 `laser_grbl_tool` 或 `laser_network_grbl_tool` 的 `send_file` 门禁和设备在线检查。

网络完整文件发送继续使用现有网络激光工具的安全规则；不要用 HTTP 逐行发送完整任务，不要绕过底层门禁自行发送 G-code。

串口发送路径会先用 `laser_grbl_tool.prepare_gcode_file_for_sending()` 扫描文件边界，再在确认后做串口 `?` 在线探测。网络发送路径委托 `laser_network_grbl_tool.send_network_file()`，完整文件强制使用 Telnet，确认后先做 Telnet `?` 在线探测。

`connection_mode` 默认为 `serial`，需要网络发送时必须提供 `host` 或配置 `LASER_NETWORK_HOST`；工具不会从历史对话猜测设备 IP。

## AI 生图首版禁用

`source_type="ai-image"` 在首版 MCP 语音入口默认禁用。工具会返回“AI 生图素材首版暂未开放”的提示，不调用外部 AI 生图 API。

AI 图片分析和 provider 配置检查也不能作为安全放行依据。是否可发送仍由本地 summary、安全报告、材料参数和用户确认共同决定。

可选 AI 图片分析使用 OpenAI-compatible 配置：`AI_LASER_OPENAI_BASE_URL`、`AI_LASER_OPENAI_API_KEY`、`AI_LASER_OPENAI_MODEL`。未配置时，本地生成仍可工作；`test_ai_provider` 只报告配置是否完整，不会放行发送。

## 相关工具边界

- `generate_text_image_tool` 适合只生成白底黑字文字图片，不转 G-code、不发送机器。
- `generate_text_image_gcode_tool` 适合“把文字直接做成图片和 G-code 文件”，只生成文件，不保留文字任务反馈历史。
- `generate_text_laser_task_tool` 适合“帮我刻 xxx”，会写任务记录、使用材料参数、支持反馈调参和重新生成。
- `ai_laser_gcode_tool` 适合图片、图片 URL、候选图、最近素材、`laser_inputs/` 最新图片或通用文字素材转 G-code/NC，并提供 `speech` 和 `summary.json` 契约。
- 三者都不能把“生成成功”解释成“已经启动机器”。小智/Web 多步任务优先进入 `laser_workflow_tool` 的 `preview` / `confirm_send`；图片 summary 的底层发送仍可通过 `send_generated`，调参正式任务可走 `start_tuned_job_tool`，明确已有文件才走底层 `send_file` 的确认流程。

## 小智提示词对齐建议

把下面规则同步到小智控制台提示词或同一个知识库条目中。提示词是辅助约束，真正安全边界仍在工具代码中。

```text
图片/通用文字素材转 G-code 或 NC 优先使用 ai_laser_gcode_tool。action="generate" 只生成文件、处理后预览、路径预览和 summary，不启动机器；最终 generate 前必须有材料和厚度。action="inspect" 只检查；action="image_search" 只返回候选，必须等用户说“选第 N 张”或提供候选 ID 后才能生成；action="analyze_image" 只给分析建议；action="test_ai_provider" 只检查 AI 分析配置；action="send_generated" 才能发送已生成文件，但必须复用现有串口/网络发送门禁。

用户说“这张图、刚才那张图、把图片转 NC”时，素材来源按明确路径/URL/文字、最近素材、laser_inputs 最新图片、追问的顺序处理。不要猜文件名。用户没有提供材料或厚度时，不生成最终 G-code/NC，先询问材料和厚度。

生成成功后向用户播报预览是否生成、预计加工时间、模式、材料、厚度、功率、速度和下一步建议。播报不要读出完整本地路径、设备地址、API Key、token 或密码。

source_type="ai-image" 首版禁用，不要调用外部 AI 生图。AI 分析建议只能辅助理解图片，不能替代材料库、安全检查、预览确认和用户确认。

正式启动、发送完整文件、切割、运动或开激光前，必须复述文件、材料、厚度、模式、功率、速度、连接方式，并等待用户明确说“确认开始”。confirmed=true 时不能临时换文件、材料、厚度或连接方式。
```

## 维护检查

更新该能力时同时检查：

- `tools/laser_asset_gcode_tool.py` 是否仍保持单工具多 action。
- `core/ai_laser_gcode/summary.py` 是否仍写出 contract version 1 或同步更新发送校验。
- `tests/test_ai_laser_gcode_core.py` 和 `tests/test_laser_asset_gcode_tool.py` 是否覆盖核心生成、候选选择、缺参追问、禁用 AI 生图、speech 和发送门禁。
- `moss_mcp/server.py` 的 `PRIORITY_TOOLS` / `DISABLED_TOOLS` 是否仍让该工具与 `check_laser_connection_tool`、`laser_safe_action_tool`、串口/网络发送工具保持预期注册顺序。
- 小智控制台提示词或知识库是否需要同步更新。
