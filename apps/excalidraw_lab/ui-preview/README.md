# 激光创作工作台 · 界面测试版

这是 `apps/excalidraw_lab/web` 的隔离界面副本，用于验证新版布局和视觉风格。

## 隔离边界

- 不修改原前端目录。
- React 业务逻辑、API 路径、材料选择、预览生成、连接检查和发送确认流程保持一致。
- `preview_server.py` 只将静态资源目录切换到本测试版的 `dist`，后端处理器仍复用项目现有实现。
- “确认发送”不是纯视觉模拟，仍受现有设备在线检查和安全门禁约束；没有连接真实设备时请勿填写真实地址。

## 启动

```powershell
npm install
npm test
npm run build
npm run serve
```

浏览器访问 `http://127.0.0.1:5180/`。
