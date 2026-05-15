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

用法示例：
  python remix_example.py \\
    --video video1.mp4 video2.mp4 \\
    --bgm background.mp3 \\
    --output result.mp4 \\
    --duration 18

配置（优先级：命令行参数 > 环境变量 > 脚本内默认值）：
  VIDGEN_BASE_URL   API 地址，例如 https://your-api-domain.com
  VIDGEN_API_KEY    Bearer Token
  VIDGEN_PROJECT_ID 项目 ID
"""

import argparse
import mimetypes
import os
import time
from pathlib import Path

import requests

# ── 默认配置（可通过环境变量覆盖） ────────────────────────────────────────────
DEFAULT_BASE_URL   = os.environ.get("VIDGEN_BASE_URL",   "https://wing-beyond-viking-str.trycloudflare.com")
DEFAULT_API_KEY    = os.environ.get("VIDGEN_API_KEY",    "vg_G3WSPfyhwATJ_HAu9lxo0W54OvCZIFENN_nxjJErjzI")
DEFAULT_PROJECT_ID = os.environ.get("VIDGEN_PROJECT_ID", "your-project-id")


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def auth_headers(api_key: str, extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra:
        headers.update(extra)
    return headers


# ── Step 1: 上传参考视频 ───────────────────────────────────────────────────────
def upload_reference_video(base_url: str, api_key: str, project_id: str, video_path: str) -> str:
    """上传一个参考视频，返回 video_upload_id。每个视频单独调用一次。"""
    path = Path(video_path)
    content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    with path.open("rb") as f:
        resp = requests.post(
            f"{base_url}/api/projects/{project_id}/upload",
            headers=auth_headers(api_key),
            files={"file": (path.name, f, content_type)},
            timeout=300,
        )
    resp.raise_for_status()
    video_id = resp.json()["id"]
    print(f"  上传视频: {path.name} → id={video_id}")
    return video_id


# ── Step 2: 上传 BGM 音频素材 ─────────────────────────────────────────────────
def upload_bgm(base_url: str, api_key: str, project_id: str, bgm_path: str) -> str:
    """上传 BGM 音频素材，返回 material_id。"""
    path = Path(bgm_path)
    content_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    with path.open("rb") as f:
        resp = requests.post(
            f"{base_url}/api/projects/{project_id}/materials/upload",
            headers=auth_headers(api_key),
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
def create_remix_run(
    base_url: str,
    api_key: str,
    project_id: str,
    reference_video_ids: list[str],
    bgm_material_id: str,
    duration_seconds: int = 18,
    script: str = "",
    add_voiceover: bool = True,
) -> str:
    """
    发起混剪流水线，返回 run_id。
    reference_video_ids 传 ≥2 个时自动进入混剪模式。
    """
    payload = {
        "script": script,
        "reference_video_ids": reference_video_ids,
        "platform": "douyin",
        "remix_config": {
            "target_duration_seconds": duration_seconds,
            "bgm_material_id": bgm_material_id,
            "bgm_volume": 0.2,
            "bgm_mood": "cinematic",
            "add_voiceover": add_voiceover,
            "voiceover_script": None,           # None=AI 自动撰写
        },
        "voiceover_no_audio": not add_voiceover,
        "transition": "fade",
        "transition_duration": 0.4,
    }
    resp = requests.post(
        f"{base_url}/api/projects/{project_id}/pipeline",
        headers=auth_headers(api_key),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    run_id = resp.json()["id"]
    print(f"  创建混剪任务 → run_id={run_id}")
    return run_id


# ── Step 4: 查询任务状态 ───────────────────────────────────────────────────────
def get_run(base_url: str, api_key: str, project_id: str, run_id: str) -> dict:
    resp = requests.get(
        f"{base_url}/api/projects/{project_id}/pipeline/{run_id}",
        headers=auth_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 5: 确认镜头方案 ───────────────────────────────────────────────────────
def confirm_remix_plan(
    base_url: str,
    api_key: str,
    project_id: str,
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
        f"{base_url}/api/projects/{project_id}/pipeline/{run_id}/confirm-remix",
        headers=auth_headers(api_key),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 6: 下载成片 ──────────────────────────────────────────────────────────
def download_final_video(
    base_url: str,
    api_key: str,
    project_id: str,
    run_id: str,
    output_path: str = "remix_output.mp4",
) -> str:
    resp = requests.get(
        f"{base_url}/api/projects/{project_id}/pipeline/{run_id}/final-video",
        headers=auth_headers(api_key),
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
def run_remix(
    video_paths: list[str],
    bgm_path: str,
    output_path: str = "remix_output.mp4",
    duration_seconds: int = 18,
    script: str = "",
    add_voiceover: bool = True,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    project_id: str = DEFAULT_PROJECT_ID,
    poll_interval: int = 5,
) -> str:
    """
    完整混剪流程，返回本地成片路径。

    参数:
        video_paths      参考视频本地路径列表（≥2 个）
        bgm_path         BGM 音频本地路径
        output_path      成片保存路径
        duration_seconds 目标时长（秒），建议与 BGM 时长一致
        script           创作方向说明（可为空）
        add_voiceover    是否生成 AI 旁白
        base_url         API 服务地址
        api_key          Bearer Token
        project_id       项目 ID
        poll_interval    轮询间隔（秒）
    """
    if len(video_paths) < 2:
        raise ValueError("至少需要 2 个参考视频才能进入混剪模式")

    base_url = base_url.strip().rstrip("/")

    print("=== Step 1: 上传参考视频 ===")
    video_ids = [upload_reference_video(base_url, api_key, project_id, p) for p in video_paths]

    print("=== Step 2: 上传 BGM ===")
    bgm_id = upload_bgm(base_url, api_key, project_id, bgm_path)

    print("=== Step 3: 创建混剪任务 ===")
    run_id = create_remix_run(
        base_url, api_key, project_id,
        video_ids, bgm_id,
        duration_seconds=duration_seconds,
        script=script,
        add_voiceover=add_voiceover,
    )

    print("=== Step 4/5: 轮询状态 & 确认方案 ===")
    confirmed_once = False
    while True:
        run = get_run(base_url, api_key, project_id, run_id)
        status = run["status"]
        agent  = run.get("current_agent", "-")
        print(f"  status={status}  agent={agent}")

        if status == "waiting_remix_confirmation" and not confirmed_once:
            # 可通过 run["review"]["data"]["segments"] 查看镜头方案后再决策
            print("  → 镜头方案待确认，自动批准…")
            result = confirm_remix_plan(base_url, api_key, project_id, run_id, approved=True)
            print("  确认结果:", result)
            confirmed_once = True

        elif status == "completed":
            print("=== Step 6: 下载成片 ===")
            return download_final_video(base_url, api_key, project_id, run_id, output_path)

        elif status in {"failed", "cancelled"}:
            raise RuntimeError(
                f"任务结束，status={status}，error={run.get('error_message')}"
            )

        time.sleep(poll_interval)


# ── CLI 入口 ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="多视频混剪 API 调用示例")
    parser.add_argument("--video",    nargs="+", required=True, metavar="PATH",  help="参考视频路径（至少 2 个）")
    parser.add_argument("--bgm",      required=True,            metavar="PATH",  help="BGM 音频路径")
    parser.add_argument("--output",   default="remix_output.mp4",               help="成片保存路径（默认 remix_output.mp4）")
    parser.add_argument("--duration", type=int, default=18,                      help="目标时长秒数（默认 18）")
    parser.add_argument("--script",   default="",                                help="创作方向说明（可选）")
    parser.add_argument("--no-voiceover", action="store_true",                   help="不生成 AI 旁白，纯 BGM 模式")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,                  help="API 地址（也可用 VIDGEN_BASE_URL 环境变量）")
    parser.add_argument("--api-key",  default=DEFAULT_API_KEY,                   help="Bearer Token（也可用 VIDGEN_API_KEY 环境变量）")
    parser.add_argument("--project",  default=DEFAULT_PROJECT_ID,                help="项目 ID（也可用 VIDGEN_PROJECT_ID 环境变量）")
    args = parser.parse_args()

    output = run_remix(
        video_paths=args.video,
        bgm_path=args.bgm,
        output_path=args.output,
        duration_seconds=args.duration,
        script=args.script,
        add_voiceover=not args.no_voiceover,
        base_url=args.base_url,
        api_key=args.api_key,
        project_id=args.project,
    )
    print(f"\n✅ 混剪完成：{output}")


if __name__ == "__main__":
    main()
