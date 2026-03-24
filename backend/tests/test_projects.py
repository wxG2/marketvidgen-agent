"""Tests for the /api/projects endpoints."""
from __future__ import annotations

from httpx import AsyncClient


async def test_create_project(client: AsyncClient):
    resp = await client.post("/api/projects", json={"name": "Test Project"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Project"
    assert data["current_step"] == 1
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


async def test_list_projects(client: AsyncClient):
    # Create two projects
    await client.post("/api/projects", json={"name": "Project A"})
    await client.post("/api/projects", json={"name": "Project B"})

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    names = {p["name"] for p in data}
    assert "Project A" in names
    assert "Project B" in names


async def test_get_project_by_id(client: AsyncClient):
    create_resp = await client.post("/api/projects", json={"name": "Fetch Me"})
    project_id = create_resp.json()["id"]

    resp = await client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == project_id
    assert resp.json()["name"] == "Fetch Me"


async def test_get_nonexistent_project_returns_404(client: AsyncClient):
    resp = await client.get("/api/projects/nonexistent-id-1234")
    assert resp.status_code == 404


async def test_update_project(client: AsyncClient):
    create_resp = await client.post("/api/projects", json={"name": "Old Name"})
    project_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/projects/{project_id}",
        json={"name": "New Name", "current_step": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["current_step"] == 3


async def test_delete_project(client: AsyncClient):
    create_resp = await client.post("/api/projects", json={"name": "Delete Me"})
    project_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Confirm it is gone
    get_resp = await client.get(f"/api/projects/{project_id}")
    assert get_resp.status_code == 404
