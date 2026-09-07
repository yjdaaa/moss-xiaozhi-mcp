# MOSS 小智 MCP 本地能力助手

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

面向小智/MOSS 语音助手的本地 MCP 服务：通过 WebSocket 接入点把本机工具暴露给助手，并围绕文字激光任务提供预览、参数建议、材料校准和确认发送流程。

当前版本：`0.1.0`（Alpha）

## 功能概览

| 能力 | 作用 | 默认状态 |
|---|---|---|
| 文字激光任务 | 根据文字生成素材、预览和 G-code/NC，并支持反馈调参和重新生成 | 启用 |
| 图片与文字 G-code | 生成文字图片，或将图片、文字轮廓转换为激光加工文件 | 启用 |
| 激光硬件控制 | 通过串口 GRBL 或 Grbl_ESP32 网络接口检查状态、发送任务和查询命令 | 启用，发送需确认 |
| 材料参数与校准 | 查询材料库、推荐参数、生成校准矩阵并保存最佳单元格 | 启用 |
| 本机 Web 页面 | 浏览器输入文字、生成预览并在确认后提交网络发送 | 启用 |
| Excalidraw 画板 | 在网页画图并导出激光加工文件 | 实验性 |
| 摄像头识别 | 通过 ONVIF 摄像头拍照并分析画面 | 需 `ENABLED_IP_CAMERA=true` |
| 本机自动化 | 打开应用、执行命令和发送快捷键 | 启用 |

生成图片、预览、推荐参数和生成 G-code 不等于启动设备。完整文件发送、运动、切割、开激光和危险 GRBL 命令都受确认门禁保护。

## 工作方式

```text
小智/MOSS
   | WebSocket MCP_ENDPOINT
   v
moss_mcp/bridge.py
   | stdin/stdout
   v
moss_mcp/server.py (FastMCP)
   | 动态发现并调用 register_tool(mcp)
   v
tools/ ---------------> core/
   ^                      |
   |                      +-- G-code、材料、运行时和硬件逻辑
   |
浏览器 ----------------> moss_mcp/web_server.py
                          |
                          +-- 本机 Web 预览与确认流程
```

`moss_mcp.server` 会自动发现 `tools/*.py`，优先注册激光主链路模块；模块必须提供 `register_tool(mcp)` 才会注册。摄像头由环境变量控制，Home Assistant 和 LaserGRBL GUI 当前在服务端默认不注册。

## 快速安装

要求 Python `3.10+`。依赖和可选功能组统一维护在 [pyproject.toml](pyproject.toml) 中：

```bash
# 基础 MCP 服务
python -m pip install -e .

# 激光、图像和串口相关能力
python -m pip install -e ".[laser]"

# 摄像头识别
python -m pip install -e ".[vision]"

# 桌面自动化
python -m pip install -e ".[desktop]"

# 本地测试和激光测试依赖
python -m pip install -e ".[dev,laser]"
```

## 快速开始

### 1. 创建本机配置

在项目根目录复制配置模板：

```bash
# macOS / Linux
cp .env.example .env

# Windows
copy .env.example .env
```

至少填写小智/MOSS 控制台生成的 MCP 接入点：

```env
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=<your-token>
```

真实 token、设备地址、摄像头账号密码、Home Assistant token 和本机私有路径只放在 `.env`，不要提交到 Git。

### 2. 启动 MCP 服务

手动启动桥接和 MCP 服务：

```bash
python -m moss_mcp.bridge moss_mcp.server
```

Windows 用户也可以运行：

```bat
scripts\start.bat
```

该脚本会创建或复用虚拟环境，检查依赖，启动本机 Web 页面，然后启动 MCP 桥接。支持 `-SkipInstall`、`-NoBrowser`、`-Host`、`-Port` 和 `-VenvDir` 参数。

### 3. 打开本机 Web 页面

单独启动 Web 页面：

```bash
python -m moss_mcp.web_server
```

默认地址为 `http://127.0.0.1:8766/`。也可以显式指定监听地址和端口：

```bash
python -m moss_mcp.web_server --host 127.0.0.1 --port 8767
```

Windows 启动脚本会同时运行 Web 页面和 MCP 桥接；如果只需要 Web 页面，可直接执行上面的 `python -m moss_mcp.web_server` 命令。

页面中的“生成预览”只准备文字任务、预览图和 G-code；确认材料、厚度、功率、速度、次数和设备信息后，才可进入网络发送流程。不要把 Web 页面绑定到公网地址。

### 4. 连接小智/MOSS

确认控制台中的 MCP 接入点与 `.env` 中的 `MCP_ENDPOINT` 一致。连接后可以从文字任务开始，例如请求助手生成一个名字牌预览；正式发送前必须明确确认。

## MCP 工具

以下工具由当前 `tools/` 模块注册。实际可用列表还会受到环境变量和服务端禁用列表影响。

### 文字、图片与工作流

| 工具 | 用途 |
|---|---|
| `generate_text_laser_task_tool` | 创建文字激光任务、预览和 G-code/NC |
| `refine_laser_params_from_feedback_tool` | 根据加工反馈生成参数调整建议 |
| `regenerate_text_laser_task_tool` | 使用新的参数重新生成任务 |
| `generate_text_image_tool` | 生成文字素材图片 |
| `generate_text_image_gcode_tool` | 将文字素材转换为 G-code，支持栅格、轮廓和切割路径 |
| `ai_laser_gcode_tool` | 统一处理图片/文字素材、候选文件、预览摘要和生成结果 |
| `laser_workflow_tool` | 为语音、Web 和其他客户端提供预览、状态、确认发送、取消和反馈入口 |

### 激光设备

| 工具 | 用途 |
|---|---|
| `check_laser_connection_tool` | 只读检查串口和网络设备是否可达 |
| `laser_safe_action_tool` | 执行状态查询、解锁、归零、设置原点、关激光、移动、急停和软复位等语义动作 |
| `laser_grbl_tool` | 串口 GRBL 机型信息、图片转 G-code、任务发送、任务状态和命令查询 |
| `laser_network_grbl_tool` | Grbl_ESP32/ESP3D 的 HTTP/Telnet 查询、任务发送、任务状态和取消 |

完整文件发送会要求 `confirmed=true`，并在发送前执行只读在线探测。网络完整文件发送使用受控的 Telnet 传输路径；设备不可达或无响应时不会继续发送。

### 材料与校准

| 工具 | 用途 |
|---|---|
| `material_params_tool` | 查询、保存、导入和导出材料参数 |
| `recommend_laser_params_tool` | 根据材料、厚度和模式读取推荐参数 |
| `run_calibration_grid_tool` | 生成材料参数校准测试矩阵 |
| `select_calibration_cell_tool` | 选择校准矩阵中的最佳单元格并写入材料库 |
| `start_tuned_job_tool` | 使用材料推荐或校准结果准备正式任务 |

材料参数默认维护在 `.lasergrbl_materials.json`，也可以通过 `LASER_MATERIAL_PARAMS_FILE` 指定本机文件。测试矩阵和参数管理不会绕过发送确认门禁。

### 本机自动化与可选工具

| 工具 | 用途 | 默认状态 |
|---|---|---|
| `open_app_tool` | 打开本机应用或网站 | 启用 |
| `open_jsjds_website_tool` | 打开中国大学生计算机设计大赛官网 | 启用 |
| `command_execution_tool` | 执行本机终端命令 | 启用，高风险 |
| `shortcut_key_execution_tool` | 发送预定义媒体快捷键 | 启用 |
| `camera_tool`、`adjust_the_camera_view_tool` | ONVIF 摄像头识别和云台控制 | 需 `ENABLED_IP_CAMERA=true` |
| `homeasstant_tool` | Home Assistant 小米屏幕挂灯控制 | 服务端默认禁用 |
| `lasergrbl_gui_tool` | LaserGRBL 桌面 GUI 自动化 | 服务端默认禁用 |

## 配置参考

常用配置位于 [.env.example](.env.example)：

| 配置项 | 作用 |
|---|---|
| `MCP_ENDPOINT` | 小智/MOSS MCP WebSocket 接入点，必填 |
| `ENABLED_IP_CAMERA` | 是否注册 ONVIF 摄像头工具，默认 `false` |
| `LASER_DEFAULT_CONNECTION_MODE` | 默认连接模式：`network` 或 `serial` |
| `LASER_ENGRAVING_MODE` | 默认雕刻方式：`raster` 或 `outline` |
| `LASER_NETWORK_HOST` | 网络激光控制器地址 |
| `GRBL_DEFAULT_PORT`、`GRBL_BAUDRATE` | 串口 GRBL 默认端口和波特率 |
| `LASER_WEB_HOST`、`LASER_WEB_PORT` | 本机 Web 服务监听地址和端口 |
| `LASER_MATERIAL_PARAMS_FILE` | 本机材料参数库路径 |

未配置的可选能力保持安全默认值；不要为了启用功能把真实凭据写进仓库文件。

## 安全边界

- 生成图片、G-code/NC、文字任务、预览、参数推荐和反馈调参默认不连接设备。
- 发送完整文件、运动、切割、开激光、GRBL 设置修改和危险单命令必须经过代码中的确认门禁。
- 发送前会进行只读设备探测；串口或网络设备不可达时，请求直接结束，不重试、不继续发送。
- Web 页面默认只绑定 `127.0.0.1`。绑定 `0.0.0.0` 只适用于可信局域网，不应映射到公网。
- 测试中的串口、HTTP、Telnet、摄像头、GUI、子进程和本地应用都应使用 mock、fake 或临时目录。

详细安全约束见 [docs/developer/safety.md](docs/developer/safety.md) 和 [docs/laser/README.md](docs/laser/README.md)。

## 项目结构

```text
.
├─ moss_mcp/                 # MCP 桥接、工具注册和本机 Web 入口
├─ core/                     # G-code、材料、运行时和硬件共享逻辑
├─ tools/                    # MCP 工具模块，每个模块提供 register_tool(mcp)
├─ apps/
│  ├─ laser_web/             # 本机激光 Web UI
│  └─ excalidraw_lab/        # Excalidraw 激光画板和 UI 测试页面
├─ tests/                    # Python 单元测试
├─ docs/                     # 用户、开发者、激光和集成文档
├─ scripts/                  # Windows 启动脚本
├─ assets/                   # 文档和界面资源
├─ .github/workflows/        # CI：Python、Web 和 UI Preview
├─ .env.example              # 配置模板
├─ pyproject.toml            # 唯一依赖和打包配置
└─ README.md
```

## 测试与构建

运行 Python 测试：

```bash
python -m unittest discover -s tests
```

Excalidraw Web 应用的测试和构建：

```bash
cd apps/excalidraw_lab/web
npm ci
npm test
npm run build
```

UI Preview 的测试：

```bash
cd apps/excalidraw_lab/ui-preview
npm ci
npm test
```

CI 配置位于 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

## 文档导航

| 主题 | 内容 | 链接 |
|---|---|---|
| 开始使用 | 安装、配置、启动和默认安全激光流程 | [用户快速入口](docs/user/quick-start.md) |
| Windows | Windows 启动脚本和环境说明 | [Windows 指南](docs/user/windows.md) |
| 开发规范 | 代码组织、注册约定、测试和文档维护 | [开发规范](docs/developer/standards.md) |
| 安全 | 高风险能力、确认门和测试边界 | [开发者安全指南](docs/developer/safety.md) |
| 激光 | 硬件基线、网络命令、G-code 和工具边界 | [激光文档索引](docs/laser/README.md) |
| 功能 | 面向维护者的功能和模块总览 | [功能字典](docs/developer/feature-glossary.md) |
| 小智/MOSS 集成 | 控制台提示词和知识库文本归档 | [提示词归档](docs/integration/xiaozhi-prompts/README.md) |
| 贡献 | 开发环境、测试和提交约定 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 安全报告 | 漏洞报告和敏感信息处理 | [SECURITY.md](SECURITY.md) |

## 贡献与许可

新增 MCP 工具请保持 `register_tool(mcp)` 注册约定，并为校验、安全门禁和外部效果添加测试。提交前请确认没有包含 `.env`、运行产物、虚拟环境、设备凭据或本机私有路径。

本项目采用 MIT License，详见 [LICENSE](LICENSE)。
