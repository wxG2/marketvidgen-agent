from __future__ import annotations

import os
from typing import List

import json
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.material import Material
from app.models.material_selection import MaterialSelection
from app.schemas.material import (
    MaterialResponse, CategoryResponse, MaterialSelectRequest,
    MaterialSelectionResponse, MaterialsPageResponse, MaterialUploadResponse,
)
from app.services.material_service import (
    get_categories, get_materials_by_category, scan_materials,
    index_uploaded_file, delete_material, delete_category,
)

router = APIRouter(tags=["materials"])


@router.post("/api/materials/scan")
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    stats = await scan_materials(db, settings.MATERIALS_ROOT)
    return stats


@router.post("/api/materials/upload")
async def upload_materials(
    files: List[UploadFile] = File(...),
    paths: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload material files with optional relative paths."""
    import traceback as tb

    try:
        path_list = json.loads(paths) if paths else []
    except (json.JSONDecodeError, TypeError):
        path_list = []

    stats = {"files": 0, "categories_set": set(), "skipped": 0}

    for i, file in enumerate(files):
        try:
            # Determine category from relative path
            rel_path = path_list[i] if i < len(path_list) else ""
            if rel_path:
                parts = PurePosixPath(rel_path).parts
                if len(parts) >= 2:
                    category = parts[-2]
                else:
                    category = "未分类"
            else:
                category = "未分类"

            content = await file.read()
            raw_filename = file.filename or f"file_{i}"
            # Browser may send full path as filename; extract just the basename
            filename = PurePosixPath(raw_filename).name
            if not filename:
                filename = raw_filename

            print(f"[upload] file {i}: raw_filename={raw_filename!r}, filename={filename!r}, category={category!r}, size={len(content)}")

            material = await index_uploaded_file(db, settings.MATERIALS_ROOT, category, filename, content)
            if material:
                stats["files"] += 1
                stats["categories_set"].add(category)
            else:
                print(f"[upload] file {i} skipped: unsupported extension for {filename!r}")
                stats["skipped"] += 1
        except Exception as e:
            print(f"[upload] Error processing file {i} ({file.filename}): {e}")
            tb.print_exc()
            stats["skipped"] += 1

    await db.commit()
    return {
        "files": stats["files"],
        "categories": len(stats["categories_set"]),
        "skipped": stats["skipped"],
        "uploaded_items": [],
        "selected_items": [],
    }


@router.post("/api/projects/{project_id}/materials/upload", response_model=MaterialUploadResponse)
async def upload_project_materials(
    project_id: str,
    files: List[UploadFile] = File(...),
    paths: str = Form(None),
    auto_select: bool = Form(True),
    db: AsyncSession = Depends(get_db),
):
    try:
        path_list = json.loads(paths) if paths else []
    except (json.JSONDecodeError, TypeError):
        path_list = []

    stats = {"files": 0, "categories_set": set(), "skipped": 0}
    uploaded_items = []
    selected_items = []

    existing_result = await db.execute(
        select(MaterialSelection)
        .where(MaterialSelection.project_id == project_id)
        .order_by(MaterialSelection.sort_order.asc())
    )
    existing_selections = list(existing_result.scalars().all())
    sort_order = len(existing_selections)

    for i, file in enumerate(files):
        try:
            rel_path = path_list[i] if i < len(path_list) else ""
            if rel_path:
                parts = PurePosixPath(rel_path).parts
                category = parts[-2] if len(parts) >= 2 else "未分类"
            else:
                category = "未分类"

            content = await file.read()
            raw_filename = file.filename or f"file_{i}"
            filename = PurePosixPath(raw_filename).name or raw_filename
            material = await index_uploaded_file(db, settings.MATERIALS_ROOT, category, filename, content)
            if not material:
                stats["skipped"] += 1
                continue

            stats["files"] += 1
            stats["categories_set"].add(category)
            await db.flush()
            uploaded_items.append(
                {
                    "id": material.id,
                    "category": material.category,
                    "filename": material.filename,
                    "media_type": material.media_type,
                    "file_size": material.file_size,
                    "width": material.width,
                    "height": material.height,
                    "thumbnail_url": f"/api/materials/{material.id}/thumbnail",
                }
            )

            if auto_select:
                selection = MaterialSelection(
                    project_id=project_id,
                    material_id=material.id,
                    category=material.category,
                    sort_order=sort_order,
                )
                sort_order += 1
                db.add(selection)
                await db.flush()
                selected_items.append(_selection_to_response(selection, material))
        except Exception:
            stats["skipped"] += 1

    await db.commit()
    return {
        "files": stats["files"],
        "categories": len(stats["categories_set"]),
        "skipped": stats["skipped"],
        "uploaded_items": uploaded_items,
        "selected_items": selected_items,
    }


@router.get("/api/materials/categories", response_model=List[CategoryResponse])
async def list_categories(db: AsyncSession = Depends(get_db)):
    return await get_categories(db)


@router.get("/api/materials", response_model=MaterialsPageResponse)
async def list_materials(
    category: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    materials, total = await get_materials_by_category(db, category, page, page_size)
    items = []
    for m in materials:
        items.append({
            "id": m.id,
            "category": m.category,
            "filename": m.filename,
            "media_type": m.media_type,
            "file_size": m.file_size,
            "width": m.width,
            "height": m.height,
            "thumbnail_url": f"/api/materials/{m.id}/thumbnail",
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.delete("/api/materials/categories/{category}")
async def remove_category(category: str, db: AsyncSession = Depends(get_db)):
    """Delete all materials in a category."""
    count = await delete_category(db, settings.MATERIALS_ROOT, category)
    if count == 0:
        raise HTTPException(404, "Category not found or empty")
    return {"ok": True, "deleted": count}


@router.delete("/api/materials/{material_id}")
async def remove_material(material_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a single material and its file."""
    deleted = await delete_material(db, settings.MATERIALS_ROOT, material_id)
    if not deleted:
        raise HTTPException(404, "Material not found")
    return {"ok": True}


@router.get("/api/materials/{material_id}/thumbnail")
async def get_thumbnail(material_id: str, db: AsyncSession = Depends(get_db)):
    material = await db.get(Material, material_id)
    if not material:
        raise HTTPException(404, "Material not found")
    file_path = os.path.join(settings.MATERIALS_ROOT, material.file_path)
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    return FileResponse(file_path, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/api/materials/{material_id}/preview")
async def get_preview(material_id: str, db: AsyncSession = Depends(get_db)):
    material = await db.get(Material, material_id)
    if not material:
        raise HTTPException(404, "Material not found")
    file_path = os.path.join(settings.MATERIALS_ROOT, material.file_path)
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    return FileResponse(file_path)


# Selection endpoints
@router.post("/api/projects/{project_id}/materials/select", response_model=MaterialSelectionResponse)
async def select_material(
    project_id: str, data: MaterialSelectRequest, db: AsyncSession = Depends(get_db),
):
    material = await db.get(Material, data.material_id)
    if not material:
        raise HTTPException(404, "Material not found")

    existing = await db.execute(
        select(MaterialSelection).where(
            MaterialSelection.project_id == project_id,
            MaterialSelection.material_id == data.material_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Already selected")

    selection = MaterialSelection(
        project_id=project_id,
        material_id=data.material_id,
        category=data.category,
        sort_order=data.sort_order,
    )
    db.add(selection)
    await db.commit()
    await db.refresh(selection)
    return _selection_to_response(selection, material)


@router.delete("/api/projects/{project_id}/materials/select/{material_id}")
async def deselect_material(project_id: str, material_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(MaterialSelection).where(
            MaterialSelection.project_id == project_id,
            MaterialSelection.material_id == material_id,
        )
    )
    await db.commit()
    return {"ok": True}


@router.get("/api/projects/{project_id}/materials/selected", response_model=List[MaterialSelectionResponse])
async def get_selected(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MaterialSelection)
        .where(MaterialSelection.project_id == project_id)
        .order_by(MaterialSelection.sort_order)
    )
    selections = list(result.scalars().all())
    responses = []
    for sel in selections:
        material = await db.get(Material, sel.material_id)
        responses.append(_selection_to_response(sel, material))
    return responses


def _selection_to_response(sel: MaterialSelection, material: Material | None) -> dict:
    mat = None
    if material:
        mat = {
            "id": material.id,
            "category": material.category,
            "filename": material.filename,
            "media_type": material.media_type,
            "file_size": material.file_size,
            "width": material.width,
            "height": material.height,
            "thumbnail_url": f"/api/materials/{material.id}/thumbnail",
        }
    return {
        "id": sel.id,
        "material_id": sel.material_id,
        "category": sel.category,
        "sort_order": sel.sort_order,
        "material": mat,
    }
