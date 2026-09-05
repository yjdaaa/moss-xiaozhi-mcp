# 用户快速入口

这份文档只给最短可用路径。更细的激光命令、硬件基线和提示词归档请继续看链接文档，不在这里复制长表。

## 1. 配置本地环境

1. 安装依赖：执行 `pip install -e .`；激光功能再执行 `pip install -e ".[laser]"`。
2. 在项目根目录创建 `.env`，至少写入小智/MOSS 控制台生成的 MCP 接入点：

```env
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=<your-token>
```

可选能力只在实际使用时配置。例如摄像头必须显式设置 `ENABLED_IP_CAMERA=true`，Home Assistant 和 LaserGRBL GUI 当前默认不注册。不要把真实 token、设备 IP、摄像头密码、WebUI 密码或本机私有路径写进仓库文档。

## 2. 启动 MCP 连接

Windows 可运行 `scripts/start_windows.bat`；脚本会同时启动 MCP 连接、本机 Web 文字输入页，并自动打开默认浏览器。手动只启动 MCP 时执行：

```bash
python -m moss_mcp.bridge moss_mcp.server
```

运行后，本地 `moss_mcp/bridge.py` 会读取 `.env` 的 `MCP_ENDPOINT`，把 `moss_mcp/server.py` 注册出来的工具接到小智/MOSS 后台。模块是否存在不等于已经在线，实际可用工具由 `moss_mcp/server.py` 的注册逻辑、禁用列表和 `.env` 开关决定。

## 3. 小智/MOSS 侧检查

1. 控制台里的 MCP 接入点要和本地 `.env` 的 `MCP_ENDPOINT` 对应。
2. 控制台提示词和知识库文本使用 [docs/integration/xiaozhi-prompts/latest.md](../integration/xiaozhi-prompts/latest.md) 作为仓库归档；粘贴时控制台 ≤2000、知识库 ≤200 总字符（空格计入）。
3. 如果控制台工具列表里看不到某个能力，先确认本地进程是否在运行，再确认该工具是否被 `moss_mcp/server.py` 禁用或受 `.env` 开关控制。

## 4. 本机 Web 文字输入

`scripts/start_windows.bat` 会默认启动本机网页入口并打开浏览器。如果要单独手动启动 Web 页，可以在项目根目录执行：

```bash
python -m moss_mcp.web_server
```

Windows 也可以双击 `scripts/start_web.bat` 单独启动 Web 页；它不会启动 MCP bridge。需要指定端口或不自动打开浏览器时，可以执行：

```powershell
scripts\start_web.bat -Port 8767 -NoBrowser
```

默认访问 `http://127.0.0.1:8766/`。页面里输入文字、材料、厚度、雕刻/切割模式和网络设备信息后，先点“生成预览”。生成步骤只创建文字任务、预览图和 G-code，不连接激光机。

页面默认选择“优先复用已有 G-code”。后端会在 `LASER_PREPARED_GCODE_DIR` 指向的目录中按文字内容查找 `.gcode` / `.nc`：先精确匹配文件名，再做包含匹配；唯一命中时把该文件纳入 workflow 预览，多命中时提示用户指定文件。关闭该选项后才会强制按文字重新生成 G-code。

确认预览、材料、厚度、功率、速度、次数和网络设备都正确后，再点“确认网络发送”。这个按钮会通过 `laser_workflow_tool` 的 `confirm_send` 继续同一个 `workflow_id`，再委托现有调参任务 / 网络发送门禁处理，仍然需要 `confirmed=true`，并在发送前做设备在线检查。

需要让同一局域网设备访问时，可以显式绑定地址，例如 `python -m moss_mcp.web_server --host 0.0.0.0 --port 8766`。不要把这个控制页面暴露到公网；真实设备地址、token、密码和私有路径仍只放在本机 `.env` 或运行时输入里。

## 5. 默认安全激光流程

生成文件不等于启动机器。生成图片、生成 G-code/NC、生成文字激光任务、推荐参数、反馈调参和重生成文件，默认只是准备文件或建议参数，不会连接设备开始雕刻。

推荐流程：

1. 用户说“帮我刻 xxx”时，`xxx` 是文字内容，先生成统一预览。默认可在 `LASER_PREPARED_GCODE_DIR` 中复用唯一匹配的 `.gcode` / `.nc`，没有唯一命中时再生成文字激光任务草稿。缺材料或厚度时先补齐信息。
2. 先预览文件、材料、厚度、加工模式、雕刻策略、功率、速度、次数和连接方式。
3. 只有用户明确说“确认开始”后，工具才允许把 `confirmed` 置为 `true` 并尝试发送。
4. 发送完整任务前，底层工具仍会先做只读在线探测；设备不可达或无响应时，本次请求直接结束，不继续发送。
5. 用户明确要求发送已有 G-code、图片文件或默认文件时，才进入底层 `send_file` 路径。

激光相关详情见 [docs/laser/README.md](../laser/README.md)。网络控制命令参考只维护在 [docs/laser/grbl-esp32-network-commands.md](../laser/grbl-esp32-network-commands.md)。
