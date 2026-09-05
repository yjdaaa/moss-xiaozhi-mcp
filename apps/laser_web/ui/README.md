# 光铸 · LASER FORGE — 激光创作工作台（视觉测试版）

`apps/laser_web/ui/` 是激光雕刻 Web 控制台的**独立视觉测试版**。单文件 HTML，纯本地模拟数据，不依赖后端、不修改主程序，双击即可打开。

## 快速预览

```powershell
cd apps/laser_web/ui
python -m http.server 5181
# 浏览器访问 http://127.0.0.1:5181/
```

（直接双击 `index.html` 也可打开；Lucide 图标通过 CDN 加载，需联网。）

## 设计方向

对标 Awwwards 级视觉标准：暗色宇宙 × 激光光束 × 粒子物理 × 实验排版。

- 全站暗色 + 霓虹青 `#35e0ff` 激光主色，搭配品红 / 金色辅助
- Hero 超大标题拆字入场动画、描边字 + 渐变字、激光光束扫动、粒子星空（鼠标引力连线）
- 创作台卡片悬停 3D 倾斜、输入框聚焦光束扫过、发光滑块、扫描线预览
- 发送流程全屏遮罩：环形进度弧 + 四阶段（校验 / 生成 / 传输 / 执行）
- 终端风格任务日志事件流
- 图标统一 Lucide，界面无表情符号

## 功能清单（模拟）

| 功能 | 说明 |
| --- | --- |
| 文字雕刻 | textarea 输入（24 字限制）+ 字符计数 |
| 图片雕刻 | 拖拽 / 点击上传 PNG / JPG / WEBP，显示文件名与大小 |
| G-code 直接执行 | 上传 .gcode / .nc / .txt，解析行数 |
| 画板 | 完整 Excalidraw 画板（矢量 / 框选 / 文字 / 群组），材料 / 厚度 / 宽高 / 策略 / 设备 host 控制面板，导出 PNG / 场景 JSON |
| 材料库 | 动态加载后端材料库（`GET /api/ui/material-options`），选择材料后按 `GET /api/ui/material-recommendation` 自动套用参数；「管理材料 ↗」跳转 `/material-lab` 编辑，保存后主页自动同步 |
| 参数滑块 | 功率 / 速度 / 厚度，实时联动推荐卡 |
| 雕刻预览 | Canvas 逐行"烧出"动画 + 进度条 + ETA |
| 设备连接 | 「模拟设备在线」胶囊状态 + 测试连接按钮（模拟延迟探测） |
| 任务操作 | 测试连接 / 保存配置（localStorage 自动恢复）/ 查状态 / 取消 / 重生成 / 反馈 |
| 发送流程 | 全屏四阶段动画，支持中途取消 |
| 任务日志 | 终端风格事件流，时间戳 + 分级着色 |

## 与真实后端的对应关系

真实后端为 `moss_mcp/web_server.py`（端口 8766），本测试版的按钮与 API 一一对应：

| 测试版操作 | 真实 API |
| --- | --- |
| 生成 G-code（文字） | `POST /api/text-task/generate` |
| 生成预览（图片 / 画板） | `POST /api/workflow {action: preview}` |
| 图片 / G-code 上传 | `POST /api/upload` |
| 画板预览 | `POST /api/draw/preview` |
| 发送雕刻 | `POST /api/workflow {action: confirm_send}` |
| 测试连接 | `POST /api/connection/check` |
| 查状态 / 取消 / 重生成 / 反馈 | `POST /api/workflow {action: status / cancel / regenerate / feedback}` |
| 材料选项 | `GET /api/ui/material-options` |

数据流：上传 → 预览 → 确认发送 → 状态查询 / 取消 / 反馈，与主程序一致。

## 画板（Excalidraw）集成说明

测试版画板使用**完整 Excalidraw 库**（与真实 `/draw` 画板同版本 `@excalidraw/excalidraw@0.18.1`），通过本地打包避免 CDN 双 React 实例问题：

- `vendor/excalidraw/forge-draw.bundle.js` —— esbuild 打包的 IIFE bundle（含 React 18.3.1 + Excalidraw 0.18.1，全局 `window.ForgeDraw`）
- `vendor/excalidraw/index.css` + `vendor/excalidraw/fonts/` —— Excalidraw 样式与字体（相对路径引用）

**重新打包 bundle**（上游依赖更新后）：

```powershell
cd apps/excalidraw_lab/web
./node_modules/.bin/esbuild ../excalidraw_lab/web/src/forgeDrawEntry.js --bundle --format=iife --global-name=ForgeDraw --outfile=vendor/excalidraw/forge-draw.bundle.js
```

入口文件：`apps/excalidraw_lab/web/src/forgeDrawEntry.js`（导出 `Excalidraw`、`exportToBlob`、`React`、`createRoot`）。
