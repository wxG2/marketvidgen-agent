from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_reference_video_upload_rejects_mismatched_content(client: AsyncClient):
    project_resp = await client.post("/api/projects", json={"name": f"Invalid Upload {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]

    response = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("not-video.mp4", b"plain text, not an mp4", "video/mp4")},
    )

    assert response.status_code == 400
    assert "视频文件内容" in response.json()["detail"]


async def test_reference_video_upload_sanitizes_filename(client: AsyncClient):
    project_resp = await client.post("/api/projects", json={"name": f"Safe Upload {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"

    response = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": ("../unsafe name?.mp4", content, "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "unsafe name_.mp4"
