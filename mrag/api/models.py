from typing import Any

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str
    profile: str | None = None
    top_k: int = Field(default=5, ge=1, le=100)
    strategy: str | None = None  # hybrid | vector | keyword


class ChunkResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    score: float
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveResponse(BaseModel):
    query: str
    profile: str
    strategy: str
    results: list[ChunkResult]


class DocumentItem(BaseModel):
    id: str
    filename: str
    file_hash: str
    status: str
    created_at: str


class DocumentDetail(DocumentItem):
    extracted_text_path: str | None
    chunk_count: int


class ProfileItem(BaseModel):
    name: str
    strategy: str
    embedding_model: str
    chunking_strategy: str


class ProfileDetail(ProfileItem):
    chunk_size: int
    overlap: int
    dense_top_k: int
    keyword_top_k: int
    fusion: str
