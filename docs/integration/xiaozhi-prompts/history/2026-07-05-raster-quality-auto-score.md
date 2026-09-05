# Xiaozhi Prompt Snapshot: Raster Quality Auto Score

Date: 2026-07-05

## Delta

- 图片光栅默认走 `raster_quality_strategy="auto"`。
- 小智和 Web 共用后端自动策略：识别照片、复杂照片、文字、logo 或线稿，最多评估少量候选方向和点距，不发送机器。
- 生成成功后，`summary.auto_raster_profile` 记录候选、得分、预计时间、选中方向和点距。
- 小智播报可解释结果，例如“已自动选择 horizontal + 0.2mm，因为照片灰度层次较多...”。
- 用户明确说“速度优先”“照片质量优先”“手动点距/方向”时，分别传 `speed`、`quality` 或 `manual`。

## Safety

自动评分只影响文件生成参数，不会绕过 `laser_workflow_tool` 的预览、确认发送、状态、取消和安全门禁。
