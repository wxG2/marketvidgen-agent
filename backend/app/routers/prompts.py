from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models.prompt import PromptMessage, Prompt
from app.models.material_selection import MaterialSelection
from app.models.video_analysis import VideoAnalysis
from app.models.material import Material
from app.schemas.prompt import (
    ChatMessageRequest, ChatMessageResponse, PromptResponse,
    PromptUpdateRequest, PromptTemplate, PromptBindingResponse,
)
from app.services.llm_service import LLMService

router = APIRouter(tags=["prompts"])

PROMPT_TEMPLATES = [
    {
        "name": "商业宣传",
        "description": "适用于商铺、商场等商业空间的宣传视频",
        "template": "请帮我生成一个商业宣传风格的视频，要求画面流畅、色调温暖，突出空间的高端感和舒适氛围。",
    },
    {
        "name": "地产展示",
        "description": "适用于房地产项目的环境和户型展示",
        "template": "请帮我生成一个地产展示视频，需要大气的航拍镜头和精致的室内漫游，体现项目品质。",
    },
    {
        "name": "文旅宣传",
        "description": "适用于旅游景点、文化场所的宣传",
        "template": "请帮我生成一个文旅宣传视频，需要展现自然风光和人文底蕴，镜头要有叙事感。",
    },
    {
        "name": "品牌故事",
        "description": "适用于品牌形象宣传片",
        "template": "请帮我生成一个品牌故事视频，从细节到全局逐步展开，体现品牌调性和核心价值。",
    },
]


def get_prompt_router(llm: LLMService) -> APIRouter:
    @router.get("/api/prompts/templates", response_model=List[PromptTemplate])
    async def get_templates():
        return PROMPT_TEMPLATES

    @router.get("/api/projects/{project_id}/chat", response_model=List[ChatMessageResponse])
    async def get_chat_history(project_id: str, db: AsyncSession = Depends(get_db)):
        result = await db.execute(
            select(PromptMessage)
            .where(PromptMessage.project_id == project_id)
            .order_by(PromptMessage.created_at)
        )
        return list(result.scalars().all())

    @router.post("/api/projects/{project_id}/chat")
    async def send_chat(project_id: str, data: ChatMessageRequest, db: AsyncSession = Depends(get_db)):
        user_msg = PromptMessage(project_id=project_id, role="user", content=data.content)
        db.add(user_msg)
        await db.commit()

        # Build message history
        result = await db.execute(
            select(PromptMessage)
            .where(PromptMessage.project_id == project_id)
            .order_by(PromptMessage.created_at)
        )
        messages = [{"role": m.role, "content": m.content} for m in result.scalars().all()]

        collected = []

        async def event_generator():
            async for chunk in llm.chat_stream(messages):
                collected.append(chunk)
                yield {"data": json.dumps({"content": chunk}, ensure_ascii=False)}
            # Save assistant message after streaming completes
            async with (await get_db().__anext__()) if False else db:
                pass
            full_response = "".join(collected)
            assistant_msg = PromptMessage(project_id=project_id, role="assistant", content=full_response)
            db.add(assistant_msg)
            await db.commit()

        return EventSourceResponse(event_generator())

    @router.post("/api/projects/{project_id}/prompts/generate", response_model=List[PromptResponse])
    async def generate_prompts(project_id: str, db: AsyncSession = Depends(get_db)):
        # Get selections
        sel_result = await db.execute(
            select(MaterialSelection)
            .where(MaterialSelection.project_id == project_id)
            .order_by(MaterialSelection.category, MaterialSelection.sort_order)
        )
        selections = list(sel_result.scalars().all())
        if not selections:
            raise HTTPException(400, "No materials selected")

        # Get analysis
        analysis_result = await db.execute(
            select(VideoAnalysis)
            .where(VideoAnalysis.project_id == project_id)
            .order_by(VideoAnalysis.created_at.desc())
            .limit(1)
        )
        analysis = analysis_result.scalar_one_or_none()

        # Get chat history for user intent
        chat_result = await db.execute(
            select(PromptMessage)
            .where(PromptMessage.project_id == project_id, PromptMessage.role == "user")
            .order_by(PromptMessage.created_at.desc())
            .limit(1)
        )
        last_user_msg = chat_result.scalar_one_or_none()

        context = {
            "selections": [{"id": s.id, "category": s.category} for s in selections],
            "analysis_summary": analysis.summary if analysis else "",
            "user_intent": last_user_msg.content if last_user_msg else "",
        }

        generated = await llm.generate_prompts(context)

        # Delete old prompts for this project
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(Prompt).where(Prompt.project_id == project_id))

        prompts = []
        for g in generated:
            p = Prompt(
                project_id=project_id,
                material_selection_id=g.get("material_selection_id"),
                prompt_text=g["prompt_text"],
            )
            db.add(p)
            prompts.append(p)
        await db.commit()
        for p in prompts:
            await db.refresh(p)
        return prompts

    @router.get("/api/projects/{project_id}/prompts", response_model=List[PromptResponse])
    async def get_prompts(project_id: str, db: AsyncSession = Depends(get_db)):
        result = await db.execute(
            select(Prompt).where(Prompt.project_id == project_id).order_by(Prompt.created_at)
        )
        return list(result.scalars().all())

    @router.patch("/api/projects/{project_id}/prompts/{prompt_id}", response_model=PromptResponse)
    async def update_prompt(
        project_id: str, prompt_id: str, data: PromptUpdateRequest,
        db: AsyncSession = Depends(get_db),
    ):
        prompt = await db.get(Prompt, prompt_id)
        if not prompt or prompt.project_id != project_id:
            raise HTTPException(404, "Prompt not found")
        prompt.prompt_text = data.prompt_text
        await db.commit()
        await db.refresh(prompt)
        return prompt

    @router.get("/api/projects/{project_id}/prompt-bindings", response_model=List[PromptBindingResponse])
    async def get_prompt_bindings(project_id: str, db: AsyncSession = Depends(get_db)):
        """Return prompts with their bound material info."""
        result = await db.execute(
            select(Prompt).where(Prompt.project_id == project_id).order_by(Prompt.created_at)
        )
        prompts = list(result.scalars().all())

        bindings = []
        for p in prompts:
            mat_id = None
            mat_filename = None
            mat_category = None
            mat_thumb = None
            if p.material_selection_id:
                sel = await db.get(MaterialSelection, p.material_selection_id)
                if sel:
                    mat_id = sel.material_id
                    mat_category = sel.category
                    mat = await db.get(Material, sel.material_id)
                    if mat:
                        mat_filename = mat.filename
                        mat_thumb = f"/api/materials/{mat.id}/thumbnail"
            bindings.append({
                "prompt_id": p.id,
                "prompt_text": p.prompt_text,
                "material_id": mat_id,
                "material_filename": mat_filename,
                "material_category": mat_category,
                "material_thumbnail_url": mat_thumb,
            })
        return bindings

    return router
