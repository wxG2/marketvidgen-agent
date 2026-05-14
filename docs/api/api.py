import json
import os
import time
import mimetypes
from pathlib import Path

import requests


BASE_URL = "https://stable-abstract-que-productions.trycloudflare.com" # 
if not BASE_URL:
    raise RuntimeError("请先设置环境变量 VIDGEN_BASE_URL，例如 https://api.yourdomain.com")

API_KEY = "vg_G3WSPfyhwATJ_HAu9lxo0W54OvCZIFENN_nxjJErjzI"
if not API_KEY:
    raise RuntimeError("请先设置环境变量 VIDGEN_API_KEY")

BASE_URL = BASE_URL.rstrip("/")
IMAGE_PATHS = [
    "/Users/weixiang/agent/vidgen/materials/等待区-人多/2024_11_19_19_35_IMG_7513.jpg",
    "/Users/weixiang/agent/vidgen/materials/门头/6ad519912j533314818693bf29485158.jpg",
]
for image_path in IMAGE_PATHS:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {path.resolve()}")
SPEC = {
    "prompt": "用这些素材生成一条抖音大健康营销视频",
    "platform": "douyin",
    "duration_seconds": 10, # 总时长
    "duration_mode": "auto", # 时长控制，fixed为固定5s时长
    "style": "commercial", # 视频风格
    "generation_model": "seedance1.5-pro", # 支持seedance2.0
    "video_model_no_audio": True, # 视频模型不要自带原声
    "voiceover_no_audio": False, # 调用配音模型生成统一的配音
    "client_reference_id": f"order-{int(time.time())}",
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
        for image_path in IMAGE_PATHS:
            path = Path(image_path)
            content_type = mimetypes.guess_type(path.name)[0] or "image/png"
            file_obj = path.open("rb")
            opened_files.append(file_obj)
            files.append(("images", (path.name, file_obj, content_type)))

        response = requests.post(
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
    response = requests.get(
        f"{BASE_URL}/v1/video-jobs/{job_id}",
        headers=auth_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def approve_review(job_id):
    response = requests.post(
        f"{BASE_URL}/v1/video-jobs/{job_id}/review",
        headers=auth_headers({"Content-Type": "application/json"}),
        json={"approved": True},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def download_result(job_id, output_path="result.mp4"):
    response = requests.get(
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
    created = create_video_job()
    job_id = created["job_id"]
    print("Created job:", json.dumps(created, ensure_ascii=False, indent=2))

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
