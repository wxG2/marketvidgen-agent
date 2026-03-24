"""Artifact cleanup — delete pipeline-generated files older than a retention period."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 7


async def cleanup_old_artifacts(retention_days: int = DEFAULT_RETENTION_DAYS) -> dict:
    """Remove generated files older than `retention_days`.

    Scans GENERATED_DIR and UPLOAD_DIR/pipeline_* temp files.
    Returns summary of deleted files count and freed bytes.
    """
    cutoff = time.time() - retention_days * 86400
    deleted_count = 0
    freed_bytes = 0

    dirs_to_scan = [
        Path(settings.GENERATED_DIR),
    ]

    for scan_dir in dirs_to_scan:
        if not scan_dir.exists():
            continue
        for file_path in scan_dir.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                stat = file_path.stat()
                if stat.st_mtime < cutoff:
                    size = stat.st_size
                    file_path.unlink()
                    deleted_count += 1
                    freed_bytes += size
            except OSError as e:
                logger.warning(f"Failed to delete {file_path}: {e}")

    logger.info(
        f"Artifact cleanup: deleted {deleted_count} files, "
        f"freed {freed_bytes / 1024 / 1024:.1f} MB "
        f"(retention={retention_days}d)"
    )
    return {
        "deleted_count": deleted_count,
        "freed_bytes": freed_bytes,
        "retention_days": retention_days,
    }


async def periodic_artifact_cleanup(
    retention_days: int = DEFAULT_RETENTION_DAYS,
    interval_hours: int = 24,
):
    """Run artifact cleanup forever on a fixed cadence until cancelled."""
    while True:
        await cleanup_old_artifacts(retention_days=retention_days)
        await time_sleep_hours(interval_hours)


async def time_sleep_hours(hours: int):
    import asyncio

    await asyncio.sleep(max(hours, 1) * 3600)
