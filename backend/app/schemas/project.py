from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    current_step: Optional[int] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    current_step: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
