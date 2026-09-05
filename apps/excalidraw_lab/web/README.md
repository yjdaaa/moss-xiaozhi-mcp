# Excalidraw Laser Lab

独立画板试验版，不修改现有 `moss_mcp/web_server.py` 页面。

## 启动

```powershell
cd apps\excalidraw_lab\web
npm install
npm run build
cd ..\..
python -m apps.excalidraw_lab.server --host 0.0.0.0 --port 8777
```

然后打开：

```text
http://<Windows电脑IP>:8777/
```

## 安全边界

- 画板导出、上传和生成预览不会启动机器。
- “确认发送”仍然复用现有 `laser_workflow_tool`、`ai_laser_gcode_tool`、网络/串口发送门禁和设备在线检查。
- 浏览器端不直接连接激光控制器，也不发送任意 GRBL 命令。
