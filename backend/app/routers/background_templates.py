from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import compile_background_template, get_background_template_for_user, get_current_user
from app.database import get_db
from app.models.background_template import BackgroundTemplate, BackgroundTemplateLearningLog
from app.models.user import User
from app.schemas.background_template import (
    BackgroundTemplateCreateRequest,
    BackgroundTemplateKeywordGenerateRequest,
    BackgroundTemplateKeywordGenerateResponse,
    BackgroundTemplateLearningLogResponse,
    BackgroundTemplateResponse,
    BackgroundTemplateUpdateRequest,
)
from app.services.llm_service import LLMService


def _serialize(template: BackgroundTemplate) -> dict:
    return {
        "id": template.id,
        "user_id": template.user_id,
        "name": template.name,
        "brand_info": template.brand_info,
        "user_requirements": template.user_requirements,
        "character_name": template.character_name,
        "identity": template.identity,
        "scene_context": template.scene_context,
        "tone_style": template.tone_style,
        "visual_style": template.visual_style,
        "do_not_include": template.do_not_include,
        "notes": template.notes,
        "learned_preferences": template.learned_preferences,
        "last_learned_summary": template.last_learned_summary,
        "learning_count": template.learning_count,
        "updated_by": template.updated_by,
        "compiled_background_context": compile_background_template(template),
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _preset_templates() -> list[dict]:
    return [
        {
            "name": "科技博主",
            "brand_info": "聚焦消费电子、AI 工具、智能硬件与效率软件。品牌调性专业、清晰、可信，目标受众是关注新技术和购买决策的城市中青年。",
            "user_requirements": "适合做产品解读、功能亮点拆解、行业趋势点评和选购建议。内容要有信息密度，但表达不能过于学术。",
            "character_name": "科技向内容主理人",
            "identity": "科技测评与趋势解读博主",
            "scene_context": "常见于办公桌、直播间、产品展示台、科技感工作室，也可结合手机、电脑、耳机、智能设备等实拍素材。",
            "tone_style": "理性专业、节奏干净、观点明确，适度加入口语化总结，让复杂技术也容易理解。",
            "visual_style": "画面简洁现代，偏冷静科技感，字幕和镜头切换利落，适合蓝灰、银白、深色桌面等视觉元素。",
            "do_not_include": "避免夸大疗效式表达、避免过度营销腔、避免虚构未展示的产品能力。",
            "notes": "适合做可信赖的科技 IP，而不是纯带货主播。",
        },
        {
            "name": "大健康招商博主",
            "brand_info": "围绕大健康、营养管理、健康服务或健康连锁项目展开，品牌调性稳重、可信、偏商务，目标受众是渠道商、代理商和意向合作方。",
            "user_requirements": "适合做项目招商、模式讲解、行业机会分析、合作政策说明。要传递专业感与信任感，同时让商业机会表达得清楚。",
            "character_name": "健康产业招商主讲人",
            "identity": "大健康赛道招商与商业模式讲解博主",
            "scene_context": "常见于会议室、路演现场、企业展厅、品牌门店或商务采访场景，也可搭配图表、门店画面、产品陈列。",
            "tone_style": "成熟稳健、商务清晰、结果导向，强调趋势、价值和合作逻辑，但避免浮夸承诺。",
            "visual_style": "偏企业宣传片和商务口播风格，画面干净、明亮、可信，可适当加入绿色、白色、金色等健康行业常见配色。",
            "do_not_include": "避免医疗疗效承诺、避免绝对化收益表述、避免涉嫌违规保健宣传。",
            "notes": "更适合讲商业机会与品牌实力，不适合做强娱乐化表达。",
        },
        {
            "name": "本地生活探店博主",
            "brand_info": "聚焦餐饮、休闲娱乐、本地服务和城市体验，品牌调性真实、生动、有亲和力，目标受众是本地消费者和年轻家庭。",
            "user_requirements": "适合做门店推荐、体验测评、团购引导和消费决策内容。重点是让用户快速理解值不值得去。",
            "character_name": "城市生活体验官",
            "identity": "本地生活探店与推荐博主",
            "scene_context": "常见于餐厅、商圈、街区、店铺门头、室内体验区，适合大量实拍和第一视角镜头。",
            "tone_style": "自然热情、口语化强、节奏轻快，结论要直给，能快速给出推荐理由。",
            "visual_style": "生活化、明亮、有烟火气，适合手持镜头、近景特写、门店环境扫拍和高频转场。",
            "do_not_include": "避免过度剧本痕迹、避免虚假排队爆火描述、避免夸张贬低同类门店。",
            "notes": "更强调真实体验和消费参考价值。",
        },
        {
            "name": "品牌创始人 IP",
            "brand_info": "用于企业主理人、创始人或核心管理者打造个人 IP。品牌调性真实、坚定、有方法论，目标受众是潜在客户、合作伙伴和行业从业者。",
            "user_requirements": "适合做观点输出、创业故事、品牌理念、产品背后逻辑和行业洞察。核心是让人记住人设与价值观。",
            "character_name": "品牌主理人",
            "identity": "创始人个人 IP 与品牌代言角色",
            "scene_context": "常见于办公室、会议室、工厂、门店、访谈区或品牌工作现场，也可结合团队、产品和业务场景。",
            "tone_style": "真诚坚定、有经验感、有判断力，适当保留个人表达习惯，让内容不像标准广告词。",
            "visual_style": "偏质感商务和人物表达，镜头突出人物可信度与品牌气质，适合中近景口播、访谈和现场纪实。",
            "do_not_include": "避免空泛成功学、避免过度包装、避免与画面不符的夸张身份叙述。",
            "notes": "重点是建立长期可信人设，不只是单条视频转化。",
        },
    ]


def _normalize_keywords(text: str) -> list[str]:
    parts = re.split(r"[\s,，、;；|/]+", text or "")
    return [part.strip() for part in parts if part.strip()]


def _keyword_score(payload: dict, keywords: list[str]) -> int:
    haystack = " ".join(str(value or "") for value in payload.values()).lower()
    return sum(1 for keyword in keywords if keyword.lower() in haystack)


def _pick_preset_by_keywords(keywords: str) -> dict:
    parts = _normalize_keywords(keywords)
    presets = _preset_templates()
    if not parts:
        return presets[0]
    scored = sorted(
        ((_keyword_score(payload, parts), idx, payload) for idx, payload in enumerate(presets)),
        key=lambda item: (-item[0], item[1]),
    )
    return scored[0][2]


def _build_keyword_fallback(keywords: str, base_template: dict | None = None) -> dict:
    source = dict(base_template or _pick_preset_by_keywords(keywords))
    keyword_text = "、".join(_normalize_keywords(keywords)) or "视频角色"
    source["name"] = source.get("name") or keyword_text
    source["brand_info"] = f"{source.get('brand_info', '')} 当前重点关键词：{keyword_text}。".strip()
    source["user_requirements"] = f"{source.get('user_requirements', '')} 请围绕关键词“{keyword_text}”输出更贴合该角色定位的内容。".strip()
    source["notes"] = f"{source.get('notes', '')} 生成依据关键词：{keyword_text}。".strip()
    return {
        "name": source.get("name", keyword_text),
        "brand_info": source.get("brand_info"),
        "user_requirements": source.get("user_requirements"),
        "character_name": source.get("character_name"),
        "identity": source.get("identity"),
        "scene_context": source.get("scene_context"),
        "tone_style": source.get("tone_style"),
        "visual_style": source.get("visual_style"),
        "do_not_include": source.get("do_not_include"),
        "notes": source.get("notes"),
    }


def _format_template_context(payload: dict) -> str:
    sections = [
        ("名称", payload.get("name")),
        ("品牌信息", payload.get("brand_info")),
        ("用户需求", payload.get("user_requirements")),
        ("角色名称", payload.get("character_name")),
        ("角色身份", payload.get("identity")),
        ("场景背景", payload.get("scene_context")),
        ("语气风格", payload.get("tone_style")),
        ("视觉风格", payload.get("visual_style")),
        ("避免内容", payload.get("do_not_include")),
        ("备注", payload.get("notes")),
    ]
    return "\n".join(f"{label}：{value}" for label, value in sections if value)


async def _create_missing_presets(db: AsyncSession, user_id: str) -> list[BackgroundTemplate]:
    result = await db.execute(
        select(BackgroundTemplate.name).where(BackgroundTemplate.user_id == user_id)
    )
    existing_names = {name for name in result.scalars().all()}
    created: list[BackgroundTemplate] = []
    for payload in _preset_templates():
        if payload["name"] in existing_names:
            continue
        template = BackgroundTemplate(user_id=user_id, updated_by="user", **payload)
        db.add(template)
        created.append(template)
    if created:
        await db.commit()
        for template in created:
            await db.refresh(template)
    return created


def get_background_templates_router(llm: LLMService) -> APIRouter:
    router = APIRouter(prefix="/api/background-templates", tags=["background-templates"])

    @router.get("", response_model=list[BackgroundTemplateResponse])
    async def list_templates(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(
            select(BackgroundTemplate.id)
            .where(BackgroundTemplate.user_id == user.id)
            .limit(1)
        )
        if result.scalar_one_or_none() is None:
            await _create_missing_presets(db, user.id)

        result = await db.execute(
            select(BackgroundTemplate)
            .where(BackgroundTemplate.user_id == user.id)
            .order_by(BackgroundTemplate.updated_at.desc(), BackgroundTemplate.created_at.desc())
        )
        return [_serialize(template) for template in result.scalars().all()]

    @router.post("/import-presets", response_model=list[BackgroundTemplateResponse])
    async def import_presets(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        created = await _create_missing_presets(db, user.id)
        return [_serialize(template) for template in created]

    @router.post("/generate-from-keywords", response_model=BackgroundTemplateKeywordGenerateResponse)
    async def generate_from_keywords(
        data: BackgroundTemplateKeywordGenerateRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        keywords = (data.keywords or "").strip()
        if not keywords:
            raise HTTPException(status_code=400, detail="请输入关键词")

        base_template_payload = None
        if data.template_id:
            template = await get_background_template_for_user(db, user.id, data.template_id)
            base_template_payload = _serialize(template)

        schema = {
            "name": "background_template_keyword_generate",
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "brand_info": {"type": "string"},
                    "user_requirements": {"type": "string"},
                    "character_name": {"type": "string"},
                    "identity": {"type": "string"},
                    "scene_context": {"type": "string"},
                    "tone_style": {"type": "string"},
                    "visual_style": {"type": "string"},
                    "do_not_include": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "name",
                    "brand_info",
                    "user_requirements",
                    "character_name",
                    "identity",
                    "scene_context",
                    "tone_style",
                    "visual_style",
                    "do_not_include",
                    "notes",
                ],
            },
        }

        base_context = ""
        if base_template_payload:
            base_context = (
                "当前选中的角色模板：\n"
                f"{_format_template_context(base_template_payload)}"
            )
        else:
            matched = _pick_preset_by_keywords(keywords)
            base_context = (
                "可参考的预设角色模板：\n"
                f"{_format_template_context(matched)}"
            )

        user_prompt = (
            "请根据用户输入的关键词，生成一份可直接保存为角色背景模板的结构化信息。\n"
            "要求：\n"
            "1. 所有字段都用中文填写，内容具体，可直接用于短视频人设配置。\n"
            "2. 如果已有选中的模板或相似预设，请保留其人设骨架，但要根据关键词做针对性改写。\n"
            "3. 输出应更像“可执行的人设背景信息”，而不是泛泛介绍。\n"
            "4. 避免空话，尽量写出角色定位、适用场景、表达方式和视觉风格。\n\n"
            f"关键词：{keywords}\n\n"
            f"{base_context}"
        )

        try:
            generated, _usage = await llm.generate_structured(
                system_prompt="你是一名短视频角色设定助手，负责把关键词扩展成完整的人设背景模板。",
                user_prompt=user_prompt,
                schema=schema,
            )
            return generated
        except Exception:
            return _build_keyword_fallback(keywords, base_template=base_template_payload)

    @router.post("", response_model=BackgroundTemplateResponse)
    async def create_template(
        data: BackgroundTemplateCreateRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        template = BackgroundTemplate(user_id=user.id, updated_by="user", **data.model_dump())
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return _serialize(template)

    @router.get("/{template_id}", response_model=BackgroundTemplateResponse)
    async def get_template(
        template_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        return _serialize(await get_background_template_for_user(db, user.id, template_id))

    @router.patch("/{template_id}", response_model=BackgroundTemplateResponse)
    async def update_template(
        template_id: str,
        data: BackgroundTemplateUpdateRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        template = await get_background_template_for_user(db, user.id, template_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(template, key, value)
        template.updated_by = "user"
        await db.commit()
        await db.refresh(template)
        return _serialize(template)

    @router.delete("/{template_id}")
    async def delete_template(
        template_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        template = await get_background_template_for_user(db, user.id, template_id)
        await db.delete(template)
        await db.commit()
        return {"ok": True}

    @router.get("/{template_id}/learning-logs", response_model=list[BackgroundTemplateLearningLogResponse])
    async def list_template_learning_logs(
        template_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        template = await get_background_template_for_user(db, user.id, template_id)
        result = await db.execute(
            select(BackgroundTemplateLearningLog)
            .where(BackgroundTemplateLearningLog.template_id == template.id)
            .order_by(BackgroundTemplateLearningLog.created_at.desc())
        )
        return list(result.scalars().all())

    return router
