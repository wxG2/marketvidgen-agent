from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx


async def ensure_local_file(path_or_url: str, workdir: Optional[str] = None) -> str:
    if path_or_url.startswith(("http://", "https://")):
        # Extract clean extension from URL path (strip query params)
        from urllib.parse import urlparse
        url_path = urlparse(path_or_url).path
        suffix = Path(url_path).suffix or ".bin"
        # Ensure suffix is short and safe
        if len(suffix) > 10:
            suffix = ".mp4"
        fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=workdir)
        os.close(fd)
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            response = await client.get(path_or_url)
            response.raise_for_status()
            Path(temp_path).write_bytes(response.content)
        return temp_path
    return path_or_url


def preprocess_image_for_platform(
    image_path: str, target_w: int, target_h: int, output_dir: str
) -> str:
    """Resize/crop an image to match platform target resolution.

    Strategy: scale to cover the target area, then center-crop.
    Returns the path to the processed image.
    """
    from PIL import Image
    import uuid as _uuid

    img = Image.open(image_path)
    # Convert to RGB if necessary (e.g. RGBA, palette)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if abs(src_ratio - target_ratio) < 0.05 and abs(src_w - target_w) < 100:
        # Close enough, skip processing
        return image_path

    # Scale to cover: the image must fill the entire target area
    if src_ratio > target_ratio:
        # Source is wider: scale by height, crop width
        new_h = target_h
        new_w = int(src_w * (target_h / src_h))
    else:
        # Source is taller: scale by width, crop height
        new_w = target_w
        new_h = int(src_h * (target_w / src_w))

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop to target
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))

    os.makedirs(output_dir, exist_ok=True)
    ext = Path(image_path).suffix or ".jpg"
    out_path = os.path.join(output_dir, f"resized_{_uuid.uuid4().hex[:8]}{ext}")
    img.save(out_path, quality=95)
    return out_path


async def run_subprocess(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode("utf-8", errors="ignore"), stderr.decode("utf-8", errors="ignore")
