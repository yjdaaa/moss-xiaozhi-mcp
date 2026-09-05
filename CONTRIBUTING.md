# Contributing

感谢参与本项目。提交代码前请先阅读 `docs/developer/safety.md`，尤其是激光设备、网络控制、桌面自动化和本地命令相关部分。

## Development

```bash
pip install -e .
pip install -e ".[dev,laser]"
python -m unittest discover -s tests
```

只有不运行激光相关测试时，才需要单独安装开发依赖：

```bash
pip install -e ".[dev]"
```

## Pull Requests

- 保持变更范围清晰，避免提交运行时输出、虚拟环境或个人配置。
- 新增工具必须保留 `register_tool(mcp)` 注册入口。
- 硬件、网络、桌面自动化测试必须使用 mock，不连接真实设备。
- 不要提交 `.env`、设备 IP、token、密码、串口号或本机绝对路径。
- 用户可见行为变化需要同步更新 README 或 `docs/`。
