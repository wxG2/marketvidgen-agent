from __future__ import annotations

import os
import uuid
from pathlib import Path

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif", ".svg", ".ico", ".avif"}


async def scan_materials(db: AsyncSession, materials_root: str, user_id: str) -> dict:
    """Scan materials directory and index all files into the database."""
    root = Path(materials_root)
    if not root.exists():
        return {"error": f"Materials root not found: {materials_root}"}

    stats = {"categories": 0, "files": 0, "skipped": 0}

    user_root = root / user_id
    if not user_root.exists():
        return {"categories": 0, "files": 0, "skipped": 0}

    for category_dir in sorted(user_root.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue

        category_name = category_dir.name
        stats["categories"] += 1

        for file_path in sorted(category_dir.rglob("*")):
            if not file_path.is_file():
                continue

            ext = file_path.suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                stats["skipped"] += 1
                continue

            rel_path = str(file_path.relative_to(root))

            existing = await db.execute(select(Material).where(Material.user_id == user_id, Material.file_path == rel_path))
            if existing.scalar_one_or_none():
                continue

            material = Material(
                id=str(uuid.uuid4()),
                user_id=user_id,
                category=category_name,
                filename=file_path.name,
                file_path=rel_path,
                file_size=file_path.stat().st_size,
                media_type="image",
            )
            db.add(material)
            stats["files"] += 1

    await db.commit()
    return stats


def get_media_type(filename: str):
    """Return media type based on file extension, or None if unsupported."""
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return None


async def index_uploaded_file(
    db: AsyncSession, materials_root: str, user_id: str, category: str, filename: str, content: bytes,
):
    """Save an uploaded image to materials_root/category/ and index it in the database."""
    media_type = get_media_type(filename)
    if media_type is None:
        print(f"[index] Unsupported file type: {filename!r} (ext={Path(filename).suffix.lower()!r})")
        return None

    category_dir = Path(materials_root) / user_id / category
    category_dir.mkdir(parents=True, exist_ok=True)

    dest = category_dir / filename
    # Avoid overwriting: append suffix if exists
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = category_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    dest.write_bytes(content)
    rel_path = str(dest.relative_to(Path(materials_root)))

    material = Material(
        id=str(uuid.uuid4()),
        user_id=user_id,
        category=category,
        filename=dest.name,
        file_path=rel_path,
        file_size=len(content),
        media_type=media_type,
    )
    db.add(material)
    return material


async def get_categories(db: AsyncSession, user_id: str) -> list[dict]:
    result = await db.execute(
        select(Material.category, func.count(Material.id))
        .where(Material.user_id == user_id, Material.media_type == "image")
        .group_by(Material.category)
        .order_by(Material.category)
    )
    return [{"name": row[0], "count": row[1]} for row in result.all()]


async def get_materials_by_category(
    db: AsyncSession, user_id: str, category: str, page: int = 1, page_size: int = 50
) -> tuple[list[Material], int]:
    count_result = await db.execute(
        select(func.count(Material.id)).where(
            Material.user_id == user_id,
            Material.category == category,
            Material.media_type == "image",
        )
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Material)
        .where(
            Material.user_id == user_id,
            Material.category == category,
            Material.media_type == "image",
        )
        .order_by(Material.filename)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    materials = list(result.scalars().all())
    return materials, total


async def delete_material(db: AsyncSession, materials_root: str, user_id: str, material_id: str) -> bool:
    """Delete a single material record and its file from disk."""
    material = await db.get(Material, material_id)
    if not material or material.user_id != user_id:
        return False

    # Delete file from disk
    file_path = Path(materials_root) / material.file_path
    if file_path.exists():
        file_path.unlink()

    # Delete any selections referencing this material
    from app.models.material_selection import MaterialSelection
    await db.execute(
        delete(MaterialSelection).where(MaterialSelection.material_id == material_id)
    )

    await db.delete(material)
    await db.commit()
    return True


async def delete_category(db: AsyncSession, materials_root: str, user_id: str, category: str) -> int:
    """Delete all materials in a category and remove the directory. Returns count deleted."""
    result = await db.execute(
        select(Material).where(Material.user_id == user_id, Material.category == category)
    )
    materials = list(result.scalars().all())

    if not materials:
        return 0

    material_ids = [m.id for m in materials]

    # Delete files from disk
    for m in materials:
        file_path = Path(materials_root) / m.file_path
        if file_path.exists():
            file_path.unlink()

    # Try to remove the category directory if empty
    category_dir = Path(materials_root) / category
    if category_dir.exists():
        try:
            category_dir.rmdir()
        except OSError:
            pass  # Directory not empty (has other files)

    # Delete selections referencing these materials
    from app.models.material_selection import MaterialSelection
    for mid in material_ids:
        await db.execute(
            delete(MaterialSelection).where(MaterialSelection.material_id == mid)
        )

    # Delete all materials in category
    await db.execute(
        delete(Material).where(Material.category == category)
    )
    await db.commit()
    return len(materials)
