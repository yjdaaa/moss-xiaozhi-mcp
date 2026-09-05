# MOSS 小智 MCP 本地能力助手

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 [虾哥 MCP](https://github.com/78/mcp-calculator)（MIT 许可证）扩展的本地 MCP 服务：通过语音助手小智/MOSS 控制本机能力，核心是 **文字激光雕刻** —— 你对着手机说"帮我刻个名字牌"，它就生成预览、等你确认后再发送到激光雕刻机。

> 本工程基于虾哥 MCP 实现，原项目地址：https://github.com/78/mcp-calculator
> 也可以安装第三方 Node.js MCP 版本：https://github.com/yokochen222/moss-xiaozhi-node-mcp

## 功能一览

- **语音文字激光雕刻**：小智/MOSS 说"帮我刻 xxx"，自动生成文字任务预览和 G-code，人工确认后才发送
- **激光雕刻机控制**：串口 GRBL / Grbl_ESP32 网络控制、材料参数库、雕刻参数推荐、测试矩阵
- **本机 Web 文字输入页**：怕语音听错字？浏览器里输入文字、预览、确认，再发送
- **Excalidraw 激光画板**：网页上画图直接生成激光加工文件（实验性）
- **视觉识别**（可选）：ONVIF 摄像头，识别桌面物件
- **Home Assistant 控制**（可选，默认禁用）
- **桌面应用/快捷键控制**（可选）：打开应用、执行本地命令

## 视频展示

[![Bilibili 演示视频](https://img.shields.io/badge/Bilibili-演示视频-ea4b6f)](https://www.bilibili.com/video/BV1TpTNzbE3u/)

## 环境要求

- **Python 3.10+**（代码使用 3.10+ 语法）
- 依赖和可选功能组统一维护在 [pyproject.toml](pyproject.toml) 中（推荐使用 `pip install -e .` 安装）

## 快速开始

### 1. 安装依赖

按需安装对应可选组（没有对应硬件就不装）：

```bash
# 基础依赖（必装）
pip install -e .

# 激光雕刻（激光功能）
pip install -e ".[laser]"

# 摄像头视觉识别（可选）
pip install -e ".[vision]"

# 桌面自动化（可选）
pip install -e ".[desktop]"

# 完整测试环境（当前测试套件包含激光模块）
pip install -e ".[dev,laser]"
```

Windows 用户可运行 `scripts/start_windows.bat`，脚本会自动创建或复用虚拟环境并安装依赖。

### 2. 配置 .env

```bash
# 复制模板并填写你的配置
cp .env.example .env      # macOS / Linux
copy .env.example .env    # Windows
```

**至少需要**：在小智/MOSS 控制台生成 MCP 接入点，填入 `MCP_ENDPOINT`：

```env
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=<your-token>
```

真实 token、设备 IP、摄像头密码等敏感信息只放在本机 `.env`，**不要提交到仓库**。更多可选项见 [.env.example](.env.example)。

### 3. 启动

```bash
python -m moss_mcp.bridge moss_mcp.server
```

Windows 用户可运行 `scripts/start_windows.bat`（同时启动 MCP 连接、本机 Web 文字输入页，并自动打开浏览器）。

### 4. 在小智/MOSS 里使用

打开小智/MOSS 控制台，确认 MCP 接入点与本地 `.env` 一致，然后就可以对语音助手说：

> "帮我刻一个名字牌"

## 文档入口

| 文档 | 说明 |
|---|---|
| [docs/user/quick-start.md](docs/user/quick-start.md) | 用户快速入门：最短启动、小智/MOSS 连接、默认安全激光流程 |
| [docs/laser/README.md](docs/laser/README.md) | 激光功能细节索引 |
| [docs/developer/safety.md](docs/developer/safety.md) | 开发者安全规范：工具注册现实、高风险能力、确认门、mock-only 测试要求 |
| [docs/developer/feature-glossary.md](docs/developer/feature-glossary.md) | 功能字典 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、测试和贡献约定 |
| [SECURITY.md](SECURITY.md) | 安全边界和漏洞报告说明 |

## 安全边界（重要）

本项目包含 **激光控制** 等高风险能力，默认设计是**生成文件不等于启动机器**：

- 生成图片、G-code、文字任务、推荐参数，默认只准备文件或建议参数，**不会连接设备**
- 正式发送 G-code、开始雕刻前，必须确认文件、材料、厚度、模式、功率、速度等，并等用户明确说"确认开始"
- 底层 `send_file` 有硬约束：默认 `confirmed=false` 只返回预览；`confirmed=true` 前会做设备在线检查（只读探测）
- 设备不可达、串口不可用、GRBL 无响应时，请求直接结束，不等待、不重试、不继续发送
- 材料参数库 `.lasergrbl_materials.json` 是本机实测的唯一维护入口，不要手改调参 JSON 或源码来维护材料经验

本仓库还包含本地命令、桌面快捷键、GUI 自动化和硬件控制能力，平台差异较大。使用前请先看 [docs/developer/safety.md](docs/developer/safety.md)，Windows / macOS / Linux 的本机路径、应用名和自动化方式需要按实际环境配置。

## 许可证

MIT License，详见 [LICENSE](LICENSE)。

本项目基于 [mcp-calculator](https://github.com/78/mcp-calculator)（MIT）二次开发，保留了上游版权声明。如果你觉得有用，别忘了点个 star 支持一下。
