from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.core.base import AgentContext
from app.agents.executors.langgraph.exceptions import WaitingConfirmation, WaitingPromptReview
from app.agents.executors.langgraph.nodes import LangGraphPipelineNodeMixin
from app.agents.executors.langgraph.state import LangGraphPipelineState
from app.agents.executors.shared import PipelineExecutorSupportMixin
from app.agents.stages.audio_subtitle import AudioSubtitleAgent
from app.agents.stages.orchestrator import OrchestratorAgent
from app.agents.stages.prompt_engineer import PromptEngineerAgent
from app.agents.stages.qa_reviewer import QAReviewerAgent
from app.agents.stages.replication_planner import ReplicationPlannerAgent
from app.agents.stages.requirement_parser import RequirementParserAgent
from app.agents.stages.video_editor import VideoEditorAgent
from app.agents.stages.video_generator import VideoGeneratorAgent
from app.config import settings
from app.services.usage_service import UsageRecorder

logger = logging.getLogger(__name__)

class LangGraphPipelineExecutor(LangGraphPipelineNodeMixin, PipelineExecutorSupportMixin):
    """LangGraph-based pipeline executor with QA node, checkpoints, and HITL support.

    New in this version
    -------------------
    - **QA reviewer** node after video_editor with conditional retry routing.
    - **Checkpoint** saved to DB after every agent completes.
    - **Human-in-the-Loop prompt review**: raises ``WaitingPromptReview``
      after PromptEngineer when enabled via ``settings.HUMAN_IN_LOOP_PROMPT_REVIEW``
      or ``input_config["review_prompts"]``.
    """

    engine_name = "langgraph"

    def __init__(
        self,
        orchestrator: OrchestratorAgent,
        prompt_engineer: PromptEngineerAgent,
        audio_agent: AudioSubtitleAgent,
        video_gen_agent: VideoGeneratorAgent,
        video_editor: VideoEditorAgent,
        db_session_factory: async_sessionmaker,
        qa_reviewer: Optional[QAReviewerAgent] = None,
        replication_planner: Optional[ReplicationPlannerAgent] = None,
        requirement_parser: Optional[RequirementParserAgent] = None,
    ):
        self.requirement_parser = requirement_parser
        self.orchestrator = orchestrator
        self.replication_planner = replication_planner
        self.prompt_engineer = prompt_engineer
        self.audio_agent = audio_agent
        self.video_gen_agent = video_gen_agent
        self.video_editor = video_editor
        self.qa_reviewer = qa_reviewer
        self.db_session_factory = db_session_factory
        self.graph = self._build_graph()

    def _route_first_stage(self, state: LangGraphPipelineState) -> str:
        """Route to replication_planner when reference_video_id is present, else orchestrator."""
        input_config = state.get("input_config") or {}
        if input_config.get("reference_video_id") and self.replication_planner is not None:
            return "replication_planner"
        return "orchestrator"

    def _build_graph(self):
        builder = StateGraph(LangGraphPipelineState)
        builder.add_node("orchestrator", self._orchestrator_node)
        builder.add_node("replication_planner", self._replication_planner_node)
        builder.add_node("prompt_engineer", self._prompt_engineer_node)
        builder.add_node("audio_subtitle", self._audio_node)
        builder.add_node("video_generator", self._video_node)
        builder.add_node("video_editor", self._editor_node)

        builder.add_conditional_edges(
            START,
            self._route_first_stage,
            {
                "orchestrator": "orchestrator",
                "replication_planner": "replication_planner",
            },
        )
        builder.add_edge("orchestrator", "prompt_engineer")
        builder.add_edge("replication_planner", "prompt_engineer")
        builder.add_edge("prompt_engineer", "audio_subtitle")
        builder.add_edge("prompt_engineer", "video_generator")
        builder.add_edge("audio_subtitle", "video_editor")
        builder.add_edge("video_generator", "video_editor")

        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            builder.add_node("qa_reviewer", self._qa_node)
            builder.add_edge("video_editor", "qa_reviewer")
            builder.add_conditional_edges(
                "qa_reviewer",
                self._qa_routing,
                {
                    "pass": END,
                    "retry_video_generator": "video_generator",
                    "retry_audio": "audio_subtitle",
                    "retry_editor": "video_editor",
                },
            )
        else:
            builder.add_edge("video_editor", END)

        return builder.compile() # compile the graph for execution

    async def run(
        self,
        pipeline_run_id: str,
        project_id: str,
        input_config: dict,
        user_id: Optional[str] = None,
        memory_service=None,
        mem0=None,
        rag_service=None,
    ) -> dict:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            db_session_factory=self.db_session_factory,
            usage_recorder=UsageRecorder(self.db_session_factory),
            artifacts={},
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
            rag_service=rag_service,
        )

        try:
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="running")
            result_state = await self.graph.ainvoke( # 初始state丢进图中，按编译后的图执行，执行完后返回最终state
                {
                    "context": context,
                    "input_config": input_config,
                    "qa_retry_count": 0,
                }
            )
            # await self._maybe_learn_background_template(context, input_config)

            final_video = (result_state.get("final_video") or {}).get("final_video_path") # 获得最终视频
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="completed",
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result_state.get("final_video", {})
        except WaitingConfirmation:
            await self._update_run(
                pipeline_run_id,
                status="waiting_confirmation",
                current_agent="replication_planner",
            )
            return {"status": "waiting_confirmation"}
        except WaitingPromptReview:
            await self._update_run(
                pipeline_run_id,
                status="waiting_prompt_review",
                current_agent="prompt_engineer",
            )
            return {"status": "waiting_prompt_review"}
        except Exception as e:
            logger.error(f"[{context.trace_id}] LangGraph pipeline failed: {e}", exc_info=True)
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="failed", error_message=str(e))
            return {"error": str(e)}
