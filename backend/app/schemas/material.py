from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class MaterialResponse(BaseModel):
    id: str
    category: str
    filename: str
    media_type: str
    file_size: Optional[int]
    width: Optional[int]
    height: Optional[int]
    thumbnail_url: Optional[str] = None

    class Config:
        from_attributes = True


class CategoryResponse(BaseModel):
    name: str
    count: int


class MaterialSelectRequest(BaseModel):
    material_id: str
    category: str
    sort_order: int = 0


class MaterialSelectionResponse(BaseModel):
    id: str
    material_id: str
    category: str
    sort_order: int
    material: Optional[MaterialResponse] = None

    class Config:
        from_attributes = True


class MaterialsPageResponse(BaseModel):
    items: List[MaterialResponse]
    total: int
    page: int
    page_size: int


class MaterialUploadResponse(BaseModel):
    files: int
    categories: int
    skipped: int
    uploaded_items: List[MaterialResponse] = []
    selected_items: List[MaterialSelectionResponse] = []
