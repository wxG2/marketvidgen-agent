"""
混剪功能 API 调用示例
=====================
演示如何通过 REST API 完成一次完整的多视频混剪：
  1. 上传参考视频（×N）
  2. 上传 BGM 音频素材
  3. 创建混剪任务
  4. 轮询状态 / 自动确认镜头方案
  5. 下载成片

依赖：pip install requests
"""

import mimetypes
import time
from pathlib import Path

import requests

# ── 配置 ──────────────────────────────────────────────────────────────────────
BASE_URL   = "https://your-api-domain.com"   # 替换为实际部署地址
API_KEY    = "your-api-key-here"             # Bearer Token
PROJECT_ID = "your-project-id"

# 本地素材路径
VIDEO_PATHS = [
    "/path/to/reference_video_1.mp4",   # 参考视频 1（例：44s）
    "/path/to/reference_video_2.mp4",   # 参考视频 2（例：19s）
]
BGM_PATH = "/path/to/background_music.mp3"   # BGM 音频（例：18s）

BASE_URL = BASE_URL.rstrip("/")


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def auth_headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if extra:
        headers.update(extra)
    return headers


# ── Step 1: 上传参考视频 ───────────────────────────────────────────────────────
def upload_reference_video(video_path: str) -> str:
    """上传一个参考视频，返回 video_upload_id。每个视频单独调用一次。"""
    path = Path(video_path)
    content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    with path.open("rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/projects/{PROJECT_ID}/upload",
            headers=auth_headers(),
            files={"file": (path.name, f, content_type)},
            timeout=300,
        )
    resp.raise_for_status()
    video_id = resp.json()["id"]
    print(f"  上传视频: {path.name} → id={video_id}")
    return video_id


# ── Step 2: 上传 BGM 音频素材 ─────────────────────────────────────────────────
def upload_bgm(bgm_path: str) -> str:
    """上传 BGM 音频素材，返回 material_id。"""
    path = Path(bgm_path)
    content_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    with path.open("rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/projects/{PROJECT_ID}/materials/upload",
            headers=auth_headers(),
            files={"files": (path.name, f, content_type)},
            data={"auto_select": "true"},
            timeout=120,
        )
    resp.raise_for_status()
    items = resp.json().get("items") or []
    if not items:
        raise RuntimeError("BGM 上传失败：接口未返回 material id")
    material_id = items[0]["id"]
    print(f"  上传 BGM: {path.name} → id={material_id}")
    return material_id


# ── Step 3: 创建混剪任务 ───────────────────────────────────────────────────────
def create_remix_run(reference_video_ids: list[str], bgm_material_id: str) -> str:
    """
    发起混剪流水线，返回 run_id。
    reference_video_ids 传 ≥2 个时自动进入混剪模式。
    """
    payload = {
        "script": "用这两段视频生成一条节奏明快的品牌混剪短片",   # 可为空字符串
        "reference_video_ids": reference_video_ids,              # ≥2 触发混剪
        "platform": "douyin",
        "remix_config": {
            "target_duration_seconds": 18,      # 精确目标时长，与 BGM 时长对齐
            "bgm_material_id": bgm_material_id, # 已上传的 BGM
            "bgm_volume": 0.2,                  # BGM 音量（0~1）
            "bgm_mood": "cinematic",
            "add_voiceover": True,              # True=生成 AI 旁白，False=纯 BGM
            "voiceover_script": None,           # None=AI 自动撰写；也可传自定义文案
        },
        "voiceover_no_audio": False,            # False=合成旁白音频；True=仅文字无声
        "transition": "fade",
        "transition_duration": 0.4,
    }
    resp = requests.post(
        f"{BASE_URL}/api/projects/{PROJECT_ID}/pipeline",
        headers=auth_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    run_id = resp.json()["id"]
    print(f"  创建混剪任务 → run_id={run_id}")
    return run_id


# ── Step 4: 查询任务状态 ───────────────────────────────────────────────────────
def get_run(run_id: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/projects/{PROJECT_ID}/pipeline/{run_id}",
        headers=auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 5: 确认镜头方案 ───────────────────────────────────────────────────────
def confirm_remix_plan(
    run_id: str,
    approved: bool = True,
    adjustments: str | None = None,
) -> dict:
    """
    当任务状态为 waiting_remix_confirmation 时调用。
      approved=True  → 直接开始合成
      approved=False → 拒绝，附上文字意见后系统重新规划
    """
    payload: dict = {"approved": approved, "edited_segments": []}
    if adjustments:
        payload["adjustments"] = adjustments
    resp = requests.post(
        f"{BASE_URL}/api/projects/{PROJECT_ID}/pipeline/{run_id}/confirm-remix",
        headers=auth_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 6: 下载成片 ──────────────────────────────────────────────────────────
def download_final_video(run_id: str, output_path: str = "remix_output.mp4") -> str:
    resp = requests.get(
        f"{BASE_URL}/api/projects/{PROJECT_ID}/pipeline/{run_id}/final-video",
        headers=auth_headers(),
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    print(f"  成片已下载 → {output_path}")
    return output_path


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    print("=== Step 1: 上传参考视频 ===")
    video_ids = [upload_reference_video(p) for p in VIDEO_PATHS]

    print("=== Step 2: 上传 BGM ===")
    bgm_id = upload_bgm(BGM_PATH)

    print("=== Step 3: 创建混剪任务 ===")
    run_id = create_remix_run(video_ids, bgm_id)

    print("=== Step 4/5: 轮询状态 & 确认方案 ===")
    poll_interval = 5   # 每 5 秒查一次
    confirmed_once = False

    while True:
        run = get_run(run_id)
        status = run["status"]
        agent  = run.get("current_agent", "-")
        print(f"  status={status}  agent={agent}")

        if status == "waiting_remix_confirmation" and not confirmed_once:
            # 可通过 run["review"]["data"]["segments"] 查看镜头方案后再决策
            print("  → 镜头方案待确认，自动批准…")
            result = confirm_remix_plan(run_id, approved=True)
            print("  确认结果:", result)
            confirmed_once = True

        elif status == "completed":
            print("=== Step 6: 下载成片 ===")
            download_final_video(run_id)
            break

        elif status in {"failed", "cancelled"}:
            raise RuntimeError(
                f"任务结束，status={status}，error={run.get('error_message')}"
            )

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
