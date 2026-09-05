# MOSS MCP Windows 用户指南

## 🪟 Windows 用户专用说明

本指南专门为 Windows 用户提供详细的 venv 环境设置和使用说明。

## 📋 系统要求

- Windows 10/11
- Python 3.7+ (推荐 Python 3.10+)
- 管理员权限（用于安装依赖）

## 🚀 快速开始

### 步骤 1: 环境设置

#### 方法一：双击运行（推荐）

1. 双击 `scripts/start_windows.bat` 文件
2. 等待脚本自动完成环境设置
3. 看到 "环境设置完成" 提示

#### 方法二：命令行运行

```cmd
# 打开命令提示符或 PowerShell
# 切换到项目目录
cd C:\path\to\moss-xiaozhi-mcp-main

# 运行设置脚本
scripts/start_windows.bat
```

### 步骤 2: 配置环境变量

确保 `.env` 文件存在并正确配置：

```env
# 小智/MOSS MCP 接入点地址
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=<your-token>

# 视觉识别相关配置
ENABLED_IP_CAMERA=false
ONVIF_CAMERA_IP=<camera-ip>
ONVIF_CAMERA_PORT=<camera-port>
ONVIF_CAMERA_USERNAME=<camera-username>
ONVIF_CAMERA_PASSWORD=<camera-password>
ONVIF_CAMERA_PTZ_ENABLED=false
ONVIF_CAMERA_LOG=false
ONVIF_CAMERA_CAPTURE=false

# Homeasstant 相关配置
HA_ADDRESS=http://<ha-host>:8123
HA_AUTH_TOKEN=<ha-token>
```

真实 token、设备 IP、摄像头账号密码、Home Assistant token、WebUI 密码和本机私有路径只放本机 `.env`，不要写入仓库文档或提示词。

### 步骤 3: 启动项目

#### 方法一：双击启动（推荐）

双击 `scripts/start_windows.bat` 文件即可自动启动项目，并会同时启动本机 Web 文字输入页、打开默认浏览器。

#### 方法二：命令行启动

```cmd
# 激活环境
.venv\\Scripts\\activate.bat

# 运行项目
python -m moss_mcp.bridge moss_mcp.server
```

## 🔧 日常使用

### 激活环境

每次使用前需要激活虚拟环境：

```cmd
# 双击运行
.venv\\Scripts\\activate.bat

# 或命令行运行
.venv\Scripts\activate.bat
```

### 运行项目

```cmd
# 快速启动
scripts/start_windows.bat

# 或手动运行
python -m moss_mcp.bridge moss_mcp.server
```

`scripts/start_windows.bat` 会默认打开 `http://127.0.0.1:8766/`。如果 `.env` 配置了 `LASER_WEB_HOST` 或 `LASER_WEB_PORT`，脚本会按配置启动 Web 页；绑定 `0.0.0.0` 时本机浏览器仍打开 `127.0.0.1`。

### 退出环境

```cmd
deactivate
```

## 🛠️ 环境管理

### 查看已安装包

```cmd
# 激活环境后
pip list
```

### 安装新依赖

```cmd
# 激活环境后
pip install 新包名

# 更新项目依赖
python -m pip install -e .
```

### 更新所有依赖

```cmd
# 激活环境后
pip install --upgrade -e .
```

## 🔍 故障排除

### 常见问题

1. **Python 未安装或未添加到 PATH**
   ```
   解决方案：
   1. 下载并安装 Python 3.10+ from https://python.org
   2. 安装时勾选 "Add Python to PATH"
   3. 重启命令提示符
   ```

2. **权限不足**
   ```
   解决方案：
   1. 以管理员身份运行命令提示符
   2. 右键点击命令提示符 → "以管理员身份运行"
   ```

3. **网络连接问题**
   ```
   解决方案：
   pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple/
   ```

4. **依赖安装失败**
   ```
   解决方案：
   1. 检查网络连接
   2. 使用国内镜像源
   3. 逐个安装依赖包
   ```

### 环境验证

运行轻量环境检查：

```cmd
# 激活环境后
python -c "import websockets, mcp, pydantic; print('core dependencies ok')"
```

## 📁 Windows 项目结构

```
moss-xiaozhi-mcp-main/
├── .venv/                  # 本机虚拟环境（不提交）
│   └── Scripts/            # Windows 激活脚本
├── scripts/start_windows.bat # 快速启动脚本
├── pyproject.toml          # 依赖和可选功能组
├── moss_mcp/bridge.py      # WebSocket/stdin/stdout 桥接入口
├── moss_mcp/server.py      # 本地 MCP 工具注册入口
├── core/                   # 核心模块
├── tools/                  # 工具模块
├── tests/                  # 单元测试
├── docs/                   # 项目文档
└── apps/                   # Web 应用和 Excalidraw 画板
```

## ⚡ 性能优化

### Windows 特定优化

1. **禁用 Windows Defender 实时保护**（仅限项目目录）
2. **使用 SSD 存储**项目文件
3. **关闭不必要的后台程序**

### 启动加速

```cmd
# 使用快速启动脚本
scripts/start_windows.bat

# 或创建桌面快捷方式
# 右键 scripts/start_windows.bat → "发送到" → "桌面快捷方式"
```

## 🔒 安全注意事项

1. **不要将 .env 文件提交到版本控制**
2. **定期更新依赖包**
3. **使用虚拟环境隔离项目**

## 📞 技术支持

如果遇到问题：

1. 查看 [用户快速开始](quick-start.md) 获取详细说明
2. 运行 `python -c "import websockets, mcp, pydantic; print('core dependencies ok')"` 检查核心依赖
3. 检查 `.env` 配置文件
4. 确保 Python 版本正确

## 🎯 Windows 用户优势

- ✅ 双击即可运行，无需命令行知识
- ✅ 图形化界面友好
- ✅ 自动环境管理
- ✅ 一键启动项目

---

**🎉 Windows 用户现在可以轻松使用 MOSS MCP 项目了！**

**所有功能保持不变，但使用更加简单便捷！**
