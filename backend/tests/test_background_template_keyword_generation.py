from app.routers.background_templates import (
    _build_keyword_fallback,
    _format_template_context,
    _pick_preset_by_keywords,
)


def test_pick_preset_by_keywords_matches_local_life_role():
    result = _pick_preset_by_keywords("城市探店 餐饮 团购推荐")
    assert result["name"] == "本地生活探店博主"


def test_keyword_fallback_preserves_base_template_and_injects_keywords():
    draft = _build_keyword_fallback(
        "科技测评 极简桌搭",
        base_template={
            "name": "科技博主",
            "brand_info": "专业讲解消费电子",
            "user_requirements": "适合做产品种草",
            "character_name": "科技主理人",
            "identity": "科技测评博主",
            "scene_context": "桌搭场景",
            "tone_style": "理性清晰",
            "visual_style": "极简科技风",
            "do_not_include": "避免夸张营销",
            "notes": "保留可信感",
        },
    )

    assert draft["name"] == "科技博主"
    assert "科技测评、极简桌搭" in (draft["brand_info"] or "")
    assert "科技测评、极简桌搭" in (draft["user_requirements"] or "")
    assert "科技测评、极简桌搭" in (draft["notes"] or "")


def test_format_template_context_outputs_human_readable_lines():
    text = _format_template_context({
        "name": "品牌创始人 IP",
        "brand_info": "品牌长期主义",
        "identity": "创始人",
    })
    assert "名称：品牌创始人 IP" in text
    assert "品牌信息：品牌长期主义" in text
    assert "角色身份：创始人" in text
