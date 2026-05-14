# Remix Video Boundaries

- 多视频混剪需要至少 2 个 `reference_video_ids`。
- 如果用户只是要求“分析这些视频”，不要启动混剪，交给 `analyze_video`。
- 如果用户只是要求“混剪方案 / 创意方案 / 分镜方案”，应保持普通对话，不启动 pipeline。
- 这个 skill 只创建 pipeline run，不等待最终成片。
