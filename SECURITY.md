# Security Policy

本项目可以控制本地命令、桌面应用和激光雕刻设备。请不要将服务直接暴露到公网，也不要在 issue、日志或提交中粘贴 token、密码、设备地址或包含隐私的运行时文件。

## Reporting

发现可能导致凭据泄露、未确认即执行硬件操作或绕过安全门的问题，请不要公开创建 issue。优先使用 GitHub Security Advisories 的私密报告入口；如果该入口不可用，请通过维护者的 GitHub 主页联系方式私下报告，并提供复现步骤、受影响版本和脱敏日志。

## Local Configuration

真实配置只放在本机 `.env`，从 `.env.example` 复制后填写。提交前请检查 Git diff 和历史，确认没有包含凭据或个人设备信息。
