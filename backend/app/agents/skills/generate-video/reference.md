# Generate Video Reference

- 当前会话素材数为 0 时，这个 skill 不可执行。
- `user_request` 可以来自用户最新消息，用来表达创作目标或营销方案要求。
- `narration_script`/`script` 只在用户明确提供可直接播报的旁白脚本时使用。
- skill 调用成功后只返回 `run_id` 和 `started` 状态，前端应通过 SSE 追踪进度。
