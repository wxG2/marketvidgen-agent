"""
混剪功能 API 调用示例
=====================

调用逻辑与 docs/api/api.py 保持一致：
  1. 使用 Authorization: Bearer vg_xxx 鉴权
  2. POST /v1/video-jobs 创建任务
  3. GET /v1/video-jobs/{job_id} 轮询状态
  4. POST /v1/video-jobs/{job_id}/review 自动确认混剪方案
  5. GET /v1/video-jobs/{job_id}/result 下载成片

依赖：pip install requests
"""

import json
import mimetypes
import os
import time
from pathlib import Path

import requests


# VidGen API 服务地址。优先读取环境变量 VIDGEN_BASE_URL；
BASE_URL = os.getenv("VIDGEN_BASE_URL", "https://wing-beyond-viking-str.trycloudflare.com")
if not BASE_URL:
    raise RuntimeError("请先设置 VIDGEN_BASE_URL，例如 https://api.yourdomain.com")

# VidGen API Key。优先读取环境变量 VIDGEN_API_KEY；
API_KEY = os.getenv("VIDGEN_API_KEY", "vg_G3WSPfyhwATJ_HAu9lxo0W54OvCZIFENN_nxjJErjzI")
if not API_KEY or API_KEY == "vg_xxx":
    raise RuntimeError("请先设置 VIDGEN_API_KEY")

BASE_URL = BASE_URL.strip().rstrip("/")
USE_ENV_PROXY = os.getenv("VIDGEN_USE_ENV_PROXY", "").lower() in {"1", "true", "yes"}

HTTP = requests.Session()
HTTP.trust_env = USE_ENV_PROXY

REFERENCE_VIDEO_PATHS = [
    "./video1.mp4",  # 必填：第 1 个参考视频本地路径，支持 mp4/mov/webm/avi
    "./video2.mp4",  # 必填：第 2 个参考视频本地路径，混剪至少需要 2 个视频
    # "./video3.mp4",  # 可选：可以继续追加更多参考视频，最多 20 个
]

# 可选：BGM 音频文件本地路径，支持 mp3/wav/aac/flac/ogg/m4a/webm。
# 如果暂时不想传 BGM，可以把 BGM_PATH 设为 None。
BGM_PATH = "./background.mp3"

# 下载后的成片保存路径。
OUTPUT_PATH = "remix_result.mp4"

for video_path in REFERENCE_VIDEO_PATHS:
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"参考视频不存在: {path.resolve()}")

bgm_path = Path(BGM_PATH) if BGM_PATH else None
if bgm_path is not None and not bgm_path.exists():
    raise FileNotFoundError(f"BGM 文件不存在: {bgm_path.resolve()}")

SPEC = {
    "prompt": "请基于这些参考视频做一条节奏感强的抖音混剪短片", #  必填：混剪创作要求。调用方通常只需要改这一项和上面的视频/BGM 文件路径。
    "client_reference_id": f"remix-{int(time.time())}",
}


def auth_headers(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if extra:
        headers.update(extra)
    return headers


def create_video_job():
    url = f"{BASE_URL}/v1/video-jobs"

    files = []
    opened_files = []

    try:
        for video_path in REFERENCE_VIDEO_PATHS:
            path = Path(video_path)
            content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
            file_obj = path.open("rb")
            opened_files.append(file_obj)
            files.append(("reference_videos", (path.name, file_obj, content_type)))

        if bgm_path is not None:
            bgm_content_type = mimetypes.guess_type(bgm_path.name)[0] or "audio/mpeg"
            bgm_file = bgm_path.open("rb")
            opened_files.append(bgm_file)
            files.append(("bgm", (bgm_path.name, bgm_file, bgm_content_type)))

        response = HTTP.post(
            url,
            headers=auth_headers({"Idempotency-Key": SPEC["client_reference_id"]}),
            data={"spec": json.dumps(SPEC, ensure_ascii=False)},
            files=files,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    finally:
        for file_obj in opened_files:
            file_obj.close()


def get_video_job(job_id):
    response = HTTP.get(
        f"{BASE_URL}/v1/video-jobs/{job_id}",
        headers=auth_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def approve_review(job_id):
    response = HTTP.post(
        f"{BASE_URL}/v1/video-jobs/{job_id}/review",
        headers=auth_headers({"Content-Type": "application/json"}),
        json={"approved": True},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def download_result(job_id, output_path=OUTPUT_PATH):
    response = HTTP.get(
        f"{BASE_URL}/v1/video-jobs/{job_id}/result",
        headers=auth_headers(),
        stream=True,
        timeout=300,
    )
    response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    return output_path


def main():
    print("BASE_URL:", BASE_URL)
    print("Use system proxy:", USE_ENV_PROXY)
    created = create_video_job()
    job_id = created["job_id"]
    print("Created remix job:", json.dumps(created, ensure_ascii=False, indent=2))

    while True:
        job = get_video_job(job_id)
        print("Current status:", job["status"], "agent:", job.get("current_agent"))

        if job["status"] == "requires_review":
            print("Review required:", job["review"]["type"])
            reviewed = approve_review(job_id)
            print("Review response:", reviewed)

        elif job["status"] == "completed":
            output_path = download_result(job_id)
            print("Downloaded:", output_path)
            break

        elif job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Job ended with status={job['status']}, error={job.get('error')}")

        time.sleep(5)


if __name__ == "__main__":
    main()
