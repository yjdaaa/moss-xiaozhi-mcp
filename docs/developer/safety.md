# 开发者安全入口

改高风险工具前先读这页，再进入源码、测试和相关项目文档。这里是入口，不替代底层契约。

## 1. 先确认运行时现实

- `moss_mcp/bridge.py` 只负责把本地 stdio MCP 脚本桥接到 `.env` 的 `MCP_ENDPOINT`。
- `moss_mcp/server.py` 才负责创建 `FastMCP("YOKO_MCP_SERVER")`、扫描 `tools/` 并调用 `register_tool(mcp)`。
- `tools.homeasstant_tool` 和 `tools.lasergrbl_gui_tool` 当前默认禁用。
- `tools.camera_tool` 只有在 `ENABLED_IP_CAMERA=true` 时注册，比较的是小写字符串 `true`。
- 不要因为 `tools/` 里有某个文件，就在文档、提示词或代码里写成默认可用。

## 2. 高风险类别

这些改动默认按高风险处理：

- 本地命令执行：`tools/command_execution_tool.py`。
- 本地应用、快捷键、GUI 自动化：`tools/open_app_tool.py`、`tools/shortcut_key_execution_tool.py`、`tools/lasergrbl_gui_tool.py`。
- 串口、网络、G-code、GRBL、材料调参、文字激光任务：所有激光相关工具。
- 本机 Web 和 Android 原生激光入口：`moss_mcp/web_server.py` 和 `android/laser-native-app` 只能复用后端 workflow、文字任务生成和既有发送门禁，不能变成前端直连设备通道。
- 摄像头和 Home Assistant：`tools/camera_tool.py`、`core/camera.py`、`tools/homeasstant_tool.py`、`core/ha.py`。

不要用 `command_execution_tool`、桌面自动化或 HTTP 逐行发送来绕过激光专用工具的确认门和在线检查。

## 3. 激光安全门

- 生成文件不等于启动机器。图片/G-code/NC 生成、文字任务、材料推荐、反馈调参和重生成默认不发送硬件。
- 文字任务、Web 预览和 Android 原生预览可通过 `reuse_prepared_gcode=true` 复用 `LASER_PREPARED_GCODE_DIR` 中唯一匹配的 `.gcode` / `.nc` 文件；这是预览/准备行为，不能绕过 `confirmed` 门和发送前在线检查。
- `send_file` 是完整任务路径；`send_command` 只适合单条手动查询或受控命令，不能代替完整雕刻/切割任务。
- 底层 `send_file` 默认 `confirmed=false` 只返回预览，不访问设备。
- `confirmed=true` 后仍必须先做只读在线探测；串口和网络完整任务都不能在探测失败后继续发送。
- 网络完整任务强制走 Telnet。HTTP 只用于只读查询或单条受控命令。
- 浏览器、手机页面、后续画板或 Android 入口都不能直接向激光控制器发送任意命令或逐行流式发送完整 G-code。
- 不猜串口号、设备 host、文件名、材料、厚度、密码、token 或用户私有路径。

具体安全规则见 [激光文档索引](../laser/README.md)、[网络控制命令参考](../laser/grbl-esp32-network-commands.md) 和 [开发规范](standards.md)。

## 4. 测试要求

- 默认验证命令：`python -m unittest discover -s tests`。
- 单元测试必须 mock 串口、HTTP、Telnet、摄像头、Home Assistant、GUI 点击、本地应用启动和 subprocess 副作用。
- 测试优先调用模块级 helper 或 fake MCP，不为了单元测试启动真实 MCP 服务。
- 运行时状态使用临时目录或 mock，不依赖本机 `.env`、真实材料库、真实设备或私有文件。

## 5. 文档和提示词同步

- 改工具名、参数、注册开关、默认连接方式或安全确认规则时，检查 Xiaozhi/MOSS 控制台提示词是否需要同步。
- 需要更新提示词或知识库时，同步修改 `docs/integration/xiaozhi-prompts/latest.md`，并在 `docs/integration/xiaozhi-prompts/history/` 新增日期化快照；粘贴正文须满足控制台 ≤2000、知识库 ≤200 总字符（空格与换行计入）。
- 功能可见变化同步 `docs/developer/feature-glossary.md`；维护规则变化同步 `docs/developer/standards.md`。
- 文档只写占位符和规则，不写真实 token、IP、串口号、账号密码或本机路径。
