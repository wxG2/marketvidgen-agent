from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agents.base import AgentContext
from app.agents.pipeline import PipelineExecutor
from app.agents.swarm_runtime import register_swarm_controller, unregister_swarm_controller
from app.models.pipeline import AgentExecution
from app.prompts import SWARM_LEAD_SYSTEM_PROMPT
from app.services.background_template_learning import learn_background_template_from_run
from app.services.usage_service import UsageRecorder

logger = logging.getLogger(__name__)


@dataclass
class SwarmTaskState:
    id: str
    agent_name: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    artifact_key: str | None = None
    input_patch: dict[str, Any] = field(default_factory=dict)
    result_summary: str | None = None
    created_by: str = "lead"


class SwarmPipelineExecutor(PipelineExecutor):
    engine_name = "swarm"
    lead_agent_name = "swarm_lead"
    checkpoint_seconds = 2.0

    async def run(self, pipeline_run_id: str, project_id: str, input_config: dict) -> dict:
        controller = register_swarm_controller(pipeline_run_id)
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            db_session_factory=self.db_session_factory,
            usage_recorder=UsageRecorder(self.db_session_factory),
            artifacts={},
        )
        try:
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="running",
                current_agent=self.lead_agent_name,
            )
            result = await self._execute_pipeline(context, input_config)
            llm = getattr(self.prompt_engineer, "llm", None)
            if llm is not None:
                await learn_background_template_from_run(
                    db_session_factory=self.db_session_factory,
                    llm=llm,
                    pipeline_run_id=pipeline_run_id,
                    input_config=input_config,
                    artifacts=context.artifacts,
                )
            final_video = result.get("final_video_path")
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="completed",
                current_agent=self.lead_agent_name,
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result
        except Exception as e:
            logger.error("[%s] Swarm pipeline failed: %s", context.trace_id, e, exc_info=True)
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="failed",
                current_agent=self.lead_agent_name,
                error_message=str(e),
            )
            return {"error": str(e)}
        finally:
            controller.update_snapshot({})
            unregister_swarm_controller(pipeline_run_id)

    async def _execute_pipeline(self, context: AgentContext, input_config: dict) -> dict:
        task_board: dict[str, SwarmTaskState] = {}
        initial_decision = await self._lead_decide(
            context=context,
            input_config=input_config,
            event_type="initial_plan",
            summary="User launched a new swarm video generation request.",
            task_board=task_board,
        )
        self._apply_actions(task_board, initial_decision.get("actions", []))
        if not task_board:
            self._seed_default_task_board(task_board)
        await self._persist_lead_decision(context, "initial_plan", initial_decision, task_board)
        return await self._run_swarm_loop(context, input_config, task_board)

    async def continue_from_retry(self, context: AgentContext, agent_name: str, input_config: dict):
        task_board = self._build_task_board_from_artifacts(context.artifacts)
        if not task_board:
            self._seed_default_task_board(task_board)
        retry_decision = await self._lead_decide(
            context=context,
            input_config=input_config,
            event_type="retry_resume",
            summary=f"Task {agent_name} was retried successfully. Decide how to continue.",
            task_board=task_board,
        )
        self._apply_actions(task_board, retry_decision.get("actions", []))
        await self._persist_lead_decision(context, "retry_resume", retry_decision, task_board)
        final = await self._run_swarm_loop(context, input_config, task_board)
        if not final:
            raise RuntimeError("Swarm retry completed without producing a final video")

    async def _run_swarm_loop(
        self,
        context: AgentContext,
        input_config: dict,
        task_board: dict[str, SwarmTaskState],
    ) -> dict:
        running: dict[asyncio.Task, SwarmTaskState] = {}

        while True:
            if await context.is_cancelled():
                raise RuntimeError("Pipeline cancelled")

            await self._drain_human_messages(context, input_config, task_board)
            self._publish_snapshot(context, task_board)

            for swarm_task in self._ready_tasks(task_board):
                swarm_task.status = "running"
                task_future = asyncio.create_task(self._run_swarm_task(context, swarm_task, input_config))
                running[task_future] = swarm_task
                self._publish_snapshot(context, task_board)

            if not running:
                if self._is_terminal(task_board):
                    closing_decision = await self._lead_decide(
                        context=context,
                        input_config=input_config,
                        event_type="closing_checkpoint",
                        summary="All currently planned swarm tasks are complete. Decide whether the run is done.",
                        task_board=task_board,
                    )
                    self._apply_actions(task_board, closing_decision.get("actions", []))
                    await self._persist_lead_decision(context, "closing_checkpoint", closing_decision, task_board)
                    self._publish_snapshot(context, task_board)
                    if self._has_final_video(context) and self._decision_is_done(closing_decision):
                        return context.artifacts.get("final_video", {})
                    if not self._has_pending_or_running(task_board):
                        if self._has_final_video(context):
                            return context.artifacts.get("final_video", {})
                        raise RuntimeError("Swarm finished but no final video artifact exists")
                    continue

                raise RuntimeError(
                    f"Swarm stalled. Pending tasks without satisfied dependencies: "
                    f"{[task.id for task in task_board.values() if task.status == 'pending']}"
                )

            done, _ = await asyncio.wait(
                running.keys(),
                timeout=self.checkpoint_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                checkpoint_decision = await self._lead_decide(
                    context=context,
                    input_config=input_config,
                    event_type="checkpoint",
                    summary="Running agents are still working. Review progress and revise the task board only if needed.",
                    task_board=task_board,
                )
                self._apply_actions(task_board, checkpoint_decision.get("actions", []))
                await self._persist_lead_decision(context, "checkpoint", checkpoint_decision, task_board)
                self._publish_snapshot(context, task_board)
                continue

            for finished in done:
                swarm_task = running.pop(finished)
                try:
                    output = finished.result()
                except Exception as exc:
                    swarm_task.status = "failed"
                    swarm_task.result_summary = str(exc)
                    self._publish_snapshot(context, task_board)
                    failure_decision = await self._lead_decide(
                        context=context,
                        input_config=input_config,
                        event_type="agent_failed",
                        summary=f"{swarm_task.agent_name} failed on task {swarm_task.id}: {exc}",
                        task_board=task_board,
                        trigger_task=swarm_task,
                    )
                    self._apply_actions(task_board, failure_decision.get("actions", []))
                    await self._persist_lead_decision(context, "agent_failed", failure_decision, task_board)
                    self._publish_snapshot(context, task_board)
                    for outstanding in running:
                        outstanding.cancel()
                    if running:
                        await asyncio.gather(*running.keys(), return_exceptions=True)
                    raise

                swarm_task.status = "completed"
                swarm_task.result_summary = self._summarize_output(output)
                artifact_key = swarm_task.artifact_key or self.get_agent_to_artifact_key().get(swarm_task.agent_name)
                if artifact_key:
                    context.artifacts[artifact_key] = output
                context.shared_workspace.setdefault("findings", {})[swarm_task.id] = {
                    "agent_name": swarm_task.agent_name,
                    "summary": swarm_task.result_summary,
                    "artifact_key": artifact_key,
                }

                self._publish_snapshot(context, task_board)
                completion_decision = await self._lead_decide(
                    context=context,
                    input_config=input_config,
                    event_type="agent_completed",
                    summary=f"{swarm_task.agent_name} completed task {swarm_task.id}.",
                    task_board=task_board,
                    trigger_task=swarm_task,
                )
                self._apply_actions(task_board, completion_decision.get("actions", []))
                await self._persist_lead_decision(context, "agent_completed", completion_decision, task_board)
                self._publish_snapshot(context, task_board)

    async def _run_swarm_task(self, context: AgentContext, swarm_task: SwarmTaskState, input_config: dict) -> dict:
        logger.info("[%s] Swarm running task %s (%s)", context.trace_id, swarm_task.id, swarm_task.agent_name)
        agent = self.get_agent_map().get(swarm_task.agent_name)
        if agent is None:
            raise RuntimeError(f"Unknown swarm agent '{swarm_task.agent_name}'")

        base_input = self.build_agent_input(swarm_task.agent_name, context.artifacts, input_config)
        merged_input = self._merge_dicts(base_input, swarm_task.input_patch)
        result = await agent.run(context, merged_input)
        if not result.success:
            raise RuntimeError(result.error or f"Agent {swarm_task.agent_name} failed")

        artifact_key = swarm_task.artifact_key or self.get_agent_to_artifact_key().get(swarm_task.agent_name)
        if artifact_key:
            context.artifacts[artifact_key] = result.output_data
        return result.output_data

    async def _drain_human_messages(self, context: AgentContext, input_config: dict, task_board: dict[str, SwarmTaskState]):
        from app.agents.swarm_runtime import get_swarm_controller

        controller = get_swarm_controller(context.pipeline_run_id)
        if controller is None:
            return

        messages = await controller.drain_human_messages()
        for message in messages:
            decision = await self._lead_decide(
                context=context,
                input_config=input_config,
                event_type="human_input",
                summary=f"Human message received: {message}",
                task_board=task_board,
                human_message=message,
            )
            self._apply_actions(task_board, decision.get("actions", []))
            await self._persist_lead_decision(context, "human_input", decision, task_board, human_message=message)
            self._publish_snapshot(context, task_board)

    async def _lead_decide(
        self,
        *,
        context: AgentContext,
        input_config: dict,
        event_type: str,
        summary: str,
        task_board: dict[str, SwarmTaskState],
        trigger_task: SwarmTaskState | None = None,
        human_message: str | None = None,
    ) -> dict[str, Any]:
        llm = getattr(self.orchestrator, "llm", None)
        fallback = self._default_lead_decision(event_type, human_message)
        if llm is None or not hasattr(llm, "generate_structured"):
            return fallback

        schema = {
            "name": "swarm_lead_decision",
            "schema": {
                "type": "object",
                "properties": {
                    "decision_summary": {"type": "string"},
                    "user_summary": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "content": {"type": "string"},
                                "create": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "agent_name": {"type": "string"},
                                            "description": {"type": "string"},
                                            "depends_on": {"type": "array", "items": {"type": "string"}},
                                            "artifact_key": {"type": "string"},
                                            "input_patch": {"type": "object"},
                                        },
                                        "required": ["id", "agent_name", "description"],
                                    },
                                },
                                "update": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "description": {"type": "string"},
                                            "artifact_key": {"type": "string"},
                                            "input_patch": {"type": "object"},
                                        },
                                        "required": ["id"],
                                    },
                                },
                                "cancel": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["decision_summary", "user_summary", "actions"],
            },
        }

        prompt_body = {
            "event_type": event_type,
            "summary": summary,
            "human_message": human_message,
            "original_request": {
                "script": input_config.get("script", ""),
                "platform": input_config.get("platform", "generic"),
                "duration_seconds": input_config.get("duration_seconds"),
                "duration_mode": input_config.get("duration_mode", "fixed"),
                "style": input_config.get("style", "commercial"),
                "bgm_mood": input_config.get("bgm_mood", "none"),
                "transition": input_config.get("transition", "none"),
            },
            "task_board": [asdict(task) for task in task_board.values()],
            "trigger_task": asdict(trigger_task) if trigger_task else None,
            "artifact_summary": self._build_artifact_summary(context.artifacts),
            "recent_events": context.events[-10:],
        }

        try:
            decision, usage = await llm.generate_structured(
                system_prompt=SWARM_LEAD_SYSTEM_PROMPT,
                user_prompt=json.dumps(prompt_body, ensure_ascii=False, default=str),
                schema=schema,
            )
            if usage:
                context.events.append(
                    {
                        "type": "lead_usage",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "usage": usage,
                    }
                )
            if not decision or not decision.get("actions"):
                return fallback
            return decision
        except Exception as exc:
            logger.warning("Lead decision failed, using fallback: %s", exc)
            return fallback

    def _default_lead_decision(self, event_type: str, human_message: str | None = None) -> dict[str, Any]:
        if event_type == "initial_plan":
            return {
                "decision_summary": "Seeded the standard capy swarm task board.",
                "user_summary": "已建立任务板，先从脚本拆镜与整体规划开始。",
                "actions": [
                    {
                        "type": "revise_plan",
                        "create": [
                            {
                                "id": "T1",
                                "agent_name": "orchestrator",
                                "description": "Analyze the request and decompose it into a shot plan.",
                                "artifact_key": "orchestrator_plan",
                            },
                            {
                                "id": "T2",
                                "agent_name": "prompt_engineer",
                                "description": "Turn the shot plan into per-shot motion prompts and voice parameters.",
                                "depends_on": ["T1"],
                                "artifact_key": "prompt_plan",
                            },
                            {
                                "id": "T3",
                                "agent_name": "audio_subtitle",
                                "description": "Produce narration audio and subtitles from the approved script and voice plan.",
                                "depends_on": ["T2"],
                                "artifact_key": "audio",
                            },
                            {
                                "id": "T4",
                                "agent_name": "video_generator",
                                "description": "Generate the shot videos from prompt-engineered prompts.",
                                "depends_on": ["T2"],
                                "artifact_key": "video_clips",
                            },
                            {
                                "id": "T5",
                                "agent_name": "video_editor",
                                "description": "Assemble the generated clips, narration, subtitles, and finishing touches.",
                                "depends_on": ["T3", "T4"],
                                "artifact_key": "final_video",
                            },
                        ],
                    },
                    {"type": "interim_reply", "content": "已完成初始分工，开始进入并行执行。"},
                ],
            }

        if event_type == "human_input" and human_message:
            transition_patch = self._extract_transition_patch(human_message)
            bgm_patch = self._extract_bgm_patch(human_message)
            input_patch = self._merge_dicts(transition_patch, bgm_patch)
            if input_patch:
                return {
                    "decision_summary": "Applied the user's preference to the editor task.",
                    "user_summary": f"已记录你的新要求：{human_message}",
                    "actions": [
                        {
                            "type": "revise_plan",
                            "update": [
                                {
                                    "id": "T5",
                                    "description": "Update the final assembly settings based on the latest user preference.",
                                    "input_patch": input_patch,
                                }
                            ],
                        },
                        {"type": "interim_reply", "content": f"已把新要求同步给后续合成阶段：{human_message}"},
                    ],
                }
            return {
                "decision_summary": "Recorded the human guidance without changing the current task graph.",
                "user_summary": f"已收到补充要求：{human_message}",
                "actions": [{"type": "interim_reply", "content": f"已收到你的补充要求：{human_message}"}],
            }

        if event_type == "closing_checkpoint":
            return {
                "decision_summary": "All required tasks are complete.",
                "user_summary": "所有任务已经完成，准备返回最终结果。",
                "actions": [{"type": "done"}],
            }

        return {
            "decision_summary": "No task-board changes required for this event.",
            "user_summary": "当前执行正常，继续推进。",
            "actions": [{"type": "noop"}],
        }

    def _apply_actions(self, task_board: dict[str, SwarmTaskState], actions: list[dict[str, Any]]):
        for action in actions:
            action_type = action.get("type")
            if action_type == "revise_plan":
                for task in action.get("create", []):
                    task_id = str(task.get("id", "")).strip()
                    agent_name = str(task.get("agent_name", "")).strip()
                    description = str(task.get("description", "")).strip()
                    if not task_id or not agent_name or not description:
                        continue
                    if task_id in task_board:
                        continue
                    task_board[task_id] = SwarmTaskState(
                        id=task_id,
                        agent_name=agent_name,
                        description=description,
                        depends_on=[str(dep) for dep in task.get("depends_on", [])],
                        artifact_key=task.get("artifact_key"),
                        input_patch=dict(task.get("input_patch") or {}),
                    )
                for update in action.get("update", []):
                    task_id = str(update.get("id", "")).strip()
                    if task_id not in task_board:
                        continue
                    task = task_board[task_id]
                    if update.get("description"):
                        task.description = str(update["description"])
                    if update.get("artifact_key"):
                        task.artifact_key = str(update["artifact_key"])
                    if isinstance(update.get("input_patch"), dict):
                        task.input_patch = self._merge_dicts(task.input_patch, update["input_patch"])
                for task_id in action.get("cancel", []):
                    if task_id in task_board and task_board[task_id].status == "pending":
                        task_board[task_id].status = "cancelled"

    async def _persist_lead_decision(
        self,
        context: AgentContext,
        event_type: str,
        decision: dict[str, Any],
        task_board: dict[str, SwarmTaskState],
        human_message: str | None = None,
    ):
        decision_summary = str(decision.get("decision_summary", ""))[:500]
        user_summary = str(decision.get("user_summary", decision_summary))[:500]
        context.events.append(
            {
                "type": "lead_decision",
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_summary": decision_summary,
                "user_summary": user_summary,
                "human_message": human_message,
            }
        )

        async with context.db_session_factory() as session:
            attempt = await self._next_lead_attempt(session, context.pipeline_run_id)
            execution = AgentExecution(
                id=str(uuid.uuid4()),
                pipeline_run_id=context.pipeline_run_id,
                trace_id=context.trace_id,
                agent_name=self.lead_agent_name,
                status="completed",
                input_data=json.dumps(
                    {
                        "event_type": event_type,
                        "human_message": human_message,
                        "task_board": [asdict(task) for task in task_board.values()],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                output_data=json.dumps(
                    {
                        "decision_summary": decision_summary,
                        "user_summary": user_summary,
                        "actions": decision.get("actions", []),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                attempt_number=attempt,
                duration_ms=0,
                completed_at=datetime.now(timezone.utc),
            )
            session.add(execution)
            await session.commit()

        await self._update_swarm_state(context, task_board, user_summary)

    async def _update_swarm_state(self, context: AgentContext, task_board: dict[str, SwarmTaskState], user_summary: str):
        state_payload = {
            "task_board": [asdict(task) for task in task_board.values()],
            "artifacts": self._build_artifact_summary(context.artifacts),
            "events": context.events[-20:],
            "latest_lead_message": user_summary,
        }
        async with context.db_session_factory() as session:
            from app.models.pipeline import PipelineRun

            run = await session.get(PipelineRun, context.pipeline_run_id)
            if run:
                run.engine = self.engine_name
                run.current_agent = self.lead_agent_name
                run.swarm_state_json = json.dumps(state_payload, ensure_ascii=False, default=str)
                run.latest_lead_message = user_summary
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()

    def _publish_snapshot(self, context: AgentContext, task_board: dict[str, SwarmTaskState]):
        from app.agents.swarm_runtime import get_swarm_controller

        serialized = [asdict(task) for task in task_board.values()]
        context.task_board = {"tasks": serialized}
        context.shared_workspace["task_board"] = serialized
        snapshot = {
            "task_board": serialized,
            "latest_lead_message": context.events[-1]["user_summary"] if context.events and context.events[-1].get("type") == "lead_decision" else None,
            "events": context.events[-10:],
        }
        controller = get_swarm_controller(context.pipeline_run_id)
        if controller is not None:
            controller.update_snapshot(snapshot)

    def _ready_tasks(self, task_board: dict[str, SwarmTaskState]) -> list[SwarmTaskState]:
        ready: list[SwarmTaskState] = []
        for task in task_board.values():
            if task.status != "pending":
                continue
            if all(task_board.get(dep) and task_board[dep].status == "completed" for dep in task.depends_on):
                ready.append(task)
        return ready

    def _seed_default_task_board(self, task_board: dict[str, SwarmTaskState]):
        default = self._default_lead_decision("initial_plan")
        self._apply_actions(task_board, default.get("actions", []))

    def _build_task_board_from_artifacts(self, artifacts: dict) -> dict[str, SwarmTaskState]:
        task_board: dict[str, SwarmTaskState] = {}
        self._seed_default_task_board(task_board)
        for task in task_board.values():
            artifact_key = task.artifact_key or self.get_agent_to_artifact_key().get(task.agent_name)
            if artifact_key and artifact_key in artifacts:
                task.status = "completed"
                task.result_summary = f"Artifact {artifact_key} already exists."
        return task_board

    def _is_terminal(self, task_board: dict[str, SwarmTaskState]) -> bool:
        for task in task_board.values():
            if task.status in {"pending", "running"}:
                return False
        return True

    def _has_final_video(self, context: AgentContext) -> bool:
        return bool(context.artifacts.get("final_video", {}).get("final_video_path"))

    def _has_pending_or_running(self, task_board: dict[str, SwarmTaskState]) -> bool:
        return any(task.status in {"pending", "running"} for task in task_board.values())

    def _decision_is_done(self, decision: dict[str, Any]) -> bool:
        return any(action.get("type") == "done" for action in decision.get("actions", []))

    def _build_artifact_summary(self, artifacts: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in artifacts.items():
            if isinstance(value, dict):
                summary[key] = {
                    "keys": list(value.keys())[:10],
                    "preview": self._summarize_output(value),
                }
            else:
                summary[key] = {"preview": str(value)[:300]}
        return summary

    def _summarize_output(self, output: dict[str, Any]) -> str:
        if "final_video_path" in output:
            return f"Final video ready at {output['final_video_path']}"
        if "video_clips" in output:
            return f"Generated {len(output.get('video_clips', []))} video clips"
        if "audio_path" in output:
            return f"Generated narration audio at {output['audio_path']}"
        if "shot_prompts" in output:
            return f"Generated {len(output.get('shot_prompts', []))} shot prompts"
        if "shots" in output:
            return f"Built shot plan with {len(output.get('shots', []))} shots"
        return json.dumps(output, ensure_ascii=False, default=str)[:300]

    def _merge_dicts(self, base: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(base or {})
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _extract_transition_patch(self, human_message: str) -> dict[str, Any]:
        msg = human_message.lower()
        if "fade" in msg or "淡入" in human_message or "淡出" in human_message:
            return {"transition": "fade"}
        if "dissolve" in msg or "溶解" in human_message:
            return {"transition": "dissolve"}
        if "slide" in msg or "滑动" in human_message:
            return {"transition": "slideright"}
        if "不要转场" in human_message or "no transition" in msg:
            return {"transition": "none"}
        return {}

    def _extract_bgm_patch(self, human_message: str) -> dict[str, Any]:
        msg = human_message.lower()
        if "calm" in msg or "舒缓" in human_message:
            return {"bgm_mood": "calm"}
        if "upbeat" in msg or "轻快" in human_message:
            return {"bgm_mood": "upbeat"}
        if "cinematic" in msg or "电影感" in human_message:
            return {"bgm_mood": "cinematic"}
        if "energetic" in msg or "动感" in human_message:
            return {"bgm_mood": "energetic"}
        if "不要bgm" in human_message or "no bgm" in msg:
            return {"bgm_mood": "none"}
        return {}

    async def _next_lead_attempt(self, session, pipeline_run_id: str) -> int:
        result = await session.execute(
            AgentExecution.__table__.select()
            .with_only_columns(AgentExecution.attempt_number)
            .where(
                AgentExecution.pipeline_run_id == pipeline_run_id,
                AgentExecution.agent_name == self.lead_agent_name,
            )
            .order_by(AgentExecution.attempt_number.desc())
            .limit(1)
        )
        row = result.first()
        return int(row[0]) + 1 if row else 1
