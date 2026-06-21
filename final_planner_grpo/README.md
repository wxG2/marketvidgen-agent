# Final Planner GRPO

面向 `wxG2/marketvidgen-agent` 的 `RemixPlannerAgent` 后训练框架。输入为“用户偏好 + 多个源视频的 ShotProfile 池”，输出严格的 `remix_plan` JSON；训练使用 TRL `GRPOTrainer` 和 LoRA。

## 已实现的约束

- Schema 是硬门控：JSON 或 `RemixPlan` 校验失败时总 reward 为 0。
- Reward 由 `source_video_id/source_shot_idx` grounding、required segment 覆盖、时长/ShotProfile 边界、叙事顺序、源视频多样性和用户约束六个软分量组成，全部可脱离训练单测。
- `remove_unused_columns=False`，候选池、槽位与约束会转发给 reward 函数。
- LoRA 下固定 `sync_ref_model=False`，不创建独立 ref model。
- 数据集由 `Dataset.from_list` 构造，不使用 `IterableDataset`。
- 评测数字只能由 `eval/evaluate.py` 生成；消融表只能由三个真实 JSON 结果汇总。

构建计划里的简化 `clips/clip_id/slot_id` 契约与目标仓库不一致。按文档“现有定义优先”的要求，本框架已对齐目标仓库 `backend/app/agents/stages/remix_planner.py`、`backend/app/prompts/system_prompts.py::REMIX_PLANNING_PROMPT` 和 `ShotProfile`：

- 输出包含 `title/concept/target_duration_seconds/source_videos/segments/audio_design/analysis_report`。
- 每段使用 `segment_idx/source_video_id/source_shot_idx/start_seconds/end_seconds`，且起止时间必须与候选 ShotProfile 完全一致。
- 输入 prompt 镜像生产 `_build_plan()` 的 `profiles/preferences` payload，不允许模型编造镜头或修改镜头边界。
- 转场、旁白、BGM strategy、voice speed/tone 与生产 planner 的归一化和校验逻辑一致。
- TRL 0.28.0 官方 `GRPOConfig` 已移除旧的 `max_prompt_length` 字段；YAML 中同名值由训练入口在 tokenizer 后做硬预算校验，超长时直接报错，不静默截断场景约束。

## 环境

要求 Python 3.10 或 3.11。固定依赖包含 `vllm==0.12.0`，建议在 Linux/CUDA 环境安装：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

macOS 本地只做 verifier 单测时，可安装最小依赖：

```bash
pip install "pydantic>=2.6" "pytest>=8" "datasets==3.5.1" "PyYAML>=6"
pytest tests/ -v
```

## 数据检查

`data/sample_prompts.jsonl` 含 8 条 train 和 2 条 eval 样例。训练 prompt 镜像生产 `_build_plan()`，展开视频 profiles、ShotProfile、用户 preferences、BGM context 及输出 schema。

```bash
python data/build_dataset.py
```

## Smoke run

Smoke 配置使用 `Qwen/Qwen2-0.5B-Instruct`、5 steps、4 generations，并关闭 vLLM：

```bash
python train/train_grpo.py --config configs/smoke.yaml
```

TRL 默认训练日志会输出 reward、组内 reward std、KL 和 completion length；`logging_steps=1` 且 `log_completions=true` 用于确认 reward 被调用和额外数据列被正确转发。`schema_metric` 和 `first_pass_qa_metric` 以权重 0 接入，只记录训练/eval 曲线，不改变 `remix_reward` 优化目标。

## 可选 SFT 冷启动

先对 base 模型执行评测。如果 `schema_valid_rate < 0.60`，再运行：

```bash
python train/sft_coldstart.py \
  --model_name_or_path Qwen/Qwen2-0.5B-Instruct \
  --output_dir outputs/sft-coldstart
```

7B instruct base 通常先直接评测，再根据实际合法率决定是否 SFT；不要凭经验填入评测数字。

## Full run

Full 配置面向单张至少 40GB 显存的 CUDA GPU，使用 colocated vLLM：

```bash
accelerate launch train/train_grpo.py --config configs/full.yaml
```

## 评测与消融

分别对 base、SFT adapter 和 GRPO adapter 贪心生成一次：

```bash
python eval/evaluate.py --model_path Qwen/Qwen2.5-7B-Instruct --split eval \
  --output_path eval/results/base.json
python eval/evaluate.py --model_path outputs/sft-coldstart --split eval \
  --output_path eval/results/sft.json
python eval/evaluate.py --model_path outputs/grpo-full --split eval \
  --output_path eval/results/grpo.json
```

每次评测同时保留时间戳文件和固定名称最新文件。指标定义如下：

- Schema 合法率：`validate_schema` 通过的样本比例。
- 首轮 QA 通过率：`schema=1, grounding=1, slot=1, duration>=0.8, constraints=1` 的样本比例。

仅在三个评测均真实完成后生成消融表：

```bash
python eval/compare_results.py \
  --base eval/results/base.json \
  --sft eval/results/sft.json \
  --grpo eval/results/grpo.json
```

这会生成 `eval/results.md` 及对应的时间戳副本，每一行都包含样本量 `n`。
