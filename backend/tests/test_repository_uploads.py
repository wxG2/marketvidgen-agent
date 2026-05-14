from __future__ import annotations

import os
import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_chat import AutoChatSession, AutoSessionReferenceVideoSelection
from app.models.material import Material
from app.models.video_upload import VideoUpload
from app.services.material_service import get_media_type


def _mp4_bytes(label: bytes = b"vidgen") -> bytes:
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + label


def _mp3_bytes(label: bytes = b"audio") -> bytes:
    return b"ID3" + label + (b"\x00" * 32)


def test_material_service_detects_audio_files():
    assert get_media_type("track.mp3") == "audio"
    assert get_media_type("track.wav") == "audio"
    assert get_media_type("image.png") == "image"


async def test_upload_video_rejects_duplicate_filename(client: AsyncClient, db: AsyncSession):
    project_resp = await client.post("/api/projects", json={"name": f"Upload Dup {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    filename = f"dup-{uuid.uuid4().hex}.mp4"

    first = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": (filename, _mp4_bytes(b"video-bytes-1"), "video/mp4")},
    )
    assert first.status_code == 200

    duplicate = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": (filename, _mp4_bytes(b"video-bytes-2"), "video/mp4")},
    )
    assert duplicate.status_code == 409
    assert "已存在于仓库中" in duplicate.json()["detail"]

    uploads = (
        await db.execute(select(VideoUpload).where(VideoUpload.filename == filename))
    ).scalars().all()
    assert len(uploads) == 1


async def test_project_material_upload_accepts_audio(client: AsyncClient, db: AsyncSession):
    project_resp = await client.post("/api/projects", json={"name": f"Audio Material {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    filename = f"bgm-{uuid.uuid4().hex}.mp3"

    response = await client.post(
        f"/api/projects/{project_id}/materials/upload",
        files={"files": (filename, _mp3_bytes(), "audio/mpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files"] == 1
    assert payload["uploaded_items"][0]["media_type"] == "audio"

    material = await db.get(Material, payload["uploaded_items"][0]["id"])
    assert material is not None
    assert material.media_type == "audio"


async def test_delete_repository_upload_removes_file_and_clears_session_reference(
    client: AsyncClient,
    db: AsyncSession,
):
    project_resp = await client.post("/api/projects", json={"name": f"Delete Upload {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]

    session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    session_id = session_resp.json()["session"]["id"]

    filename = f"delete-{uuid.uuid4().hex}.mp4"
    upload_resp = await client.post(
        f"/api/projects/{project_id}/upload",
        data={"session_id": session_id},
        files={"file": (filename, _mp4_bytes(b"video-delete-me"), "video/mp4")},
    )
    assert upload_resp.status_code == 200
    upload_id = upload_resp.json()["id"]

    upload = await db.get(VideoUpload, upload_id)
    assert upload is not None
    upload_file_path = upload.file_path
    assert os.path.exists(upload_file_path)

    session = await db.get(AutoChatSession, session_id)
    assert session is not None
    assert session.reference_video_id == upload_id

    delete_resp = await client.delete(f"/api/repository/uploads/{upload_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"ok": True}

    db.expire_all()
    assert await db.get(VideoUpload, upload_id) is None

    cleared_session = await db.get(AutoChatSession, session_id)
    assert cleared_session is not None
    assert cleared_session.reference_video_id is None

    assert not os.path.exists(upload_file_path)


async def test_import_repository_upload_creates_session_accessible_copy(
    client: AsyncClient,
    db: AsyncSession,
):
    project_resp = await client.post("/api/projects", json={"name": f"Import Upload {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]

    source_session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    source_session_id = source_session_resp.json()["session"]["id"]
    target_session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    target_session_id = target_session_resp.json()["session"]["id"]

    filename = f"repo-import-{uuid.uuid4().hex}.mp4"
    upload_resp = await client.post(
        f"/api/projects/{project_id}/upload",
        data={"session_id": source_session_id},
        files={"file": (filename, _mp4_bytes(b"video-to-import"), "video/mp4")},
    )
    assert upload_resp.status_code == 200
    source_upload_id = upload_resp.json()["id"]

    import_resp = await client.post(
        f"/api/repository/uploads/{source_upload_id}/import",
        params={"project_id": project_id, "session_id": target_session_id},
    )
    assert import_resp.status_code == 200
    imported = import_resp.json()
    imported_upload_id = imported["id"]
    assert imported_upload_id != source_upload_id
    assert imported["project_id"] == project_id
    assert imported["session_id"] == target_session_id

    db.expire_all()
    source_upload = await db.get(VideoUpload, source_upload_id)
    imported_upload = await db.get(VideoUpload, imported_upload_id)
    target_session = await db.get(AutoChatSession, target_session_id)

    assert source_upload is not None
    assert imported_upload is not None
    assert target_session is not None
    assert target_session.reference_video_id == imported_upload_id
    assert imported_upload.file_path != source_upload.file_path
    assert os.path.exists(source_upload.file_path)
    assert os.path.exists(imported_upload.file_path)

    patch_resp = await client.patch(
        f"/api/projects/{project_id}/auto-sessions/{target_session_id}",
        json={
            "reference_video_id": imported_upload_id,
            "status_preview": "参考视频已上传",
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["state"]["reference_video_id"] == imported_upload_id


async def test_auto_session_persists_multiple_reference_videos(
    client: AsyncClient,
    db: AsyncSession,
):
    project_resp = await client.post("/api/projects", json={"name": f"Multi Ref {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    session_id = session_resp.json()["session"]["id"]

    upload_ids: list[str] = []
    for index in range(2):
        upload_resp = await client.post(
            f"/api/projects/{project_id}/upload",
            data={"session_id": session_id},
            files={"file": (f"multi-ref-{uuid.uuid4().hex}-{index}.mp4", _mp4_bytes(f"video-{index}".encode()), "video/mp4")},
        )
        assert upload_resp.status_code == 200
        upload_ids.append(upload_resp.json()["id"])

    patch_resp = await client.patch(
        f"/api/projects/{project_id}/auto-sessions/{session_id}",
        json={"reference_video_ids": upload_ids},
    )
    assert patch_resp.status_code == 200
    payload = patch_resp.json()
    assert payload["state"]["reference_video_id"] == upload_ids[0]
    assert payload["state"]["reference_video_ids"] == upload_ids
    assert [item["id"] for item in payload["reference_videos"]] == upload_ids

    db.expire_all()
    session = await db.get(AutoChatSession, session_id)
    assert session is not None
    assert session.reference_video_id == upload_ids[0]
    rows = (
        await db.execute(
            select(AutoSessionReferenceVideoSelection)
            .where(AutoSessionReferenceVideoSelection.session_id == session_id)
            .order_by(AutoSessionReferenceVideoSelection.sort_order.asc())
        )
    ).scalars().all()
    assert [row.video_upload_id for row in rows] == upload_ids

    clear_resp = await client.patch(
        f"/api/projects/{project_id}/auto-sessions/{session_id}",
        json={"reference_video_ids": []},
    )
    assert clear_resp.status_code == 200
    clear_payload = clear_resp.json()
    assert clear_payload["state"]["reference_video_id"] is None
    assert clear_payload["state"]["reference_video_ids"] == []
    assert clear_payload["reference_videos"] == []

    db.expire_all()
    cleared_session = await db.get(AutoChatSession, session_id)
    assert cleared_session is not None
    assert cleared_session.reference_video_id is None
    cleared_rows = (
        await db.execute(
            select(AutoSessionReferenceVideoSelection)
            .where(AutoSessionReferenceVideoSelection.session_id == session_id)
        )
    ).scalars().all()
    assert cleared_rows == []


async def test_auto_session_rejects_reference_video_from_other_project(
    client: AsyncClient,
):
    project_resp = await client.post("/api/projects", json={"name": f"Main Ref {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    other_project_resp = await client.post("/api/projects", json={"name": f"Other Ref {uuid.uuid4().hex[:8]}"})
    other_project_id = other_project_resp.json()["id"]
    session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    session_id = session_resp.json()["session"]["id"]

    other_upload_resp = await client.post(
        f"/api/projects/{other_project_id}/upload",
        files={"file": (f"other-{uuid.uuid4().hex}.mp4", _mp4_bytes(b"other-project"), "video/mp4")},
    )
    assert other_upload_resp.status_code == 200

    patch_resp = await client.patch(
        f"/api/projects/{project_id}/auto-sessions/{session_id}",
        json={"reference_video_ids": [other_upload_resp.json()["id"]]},
    )
    assert patch_resp.status_code == 400
