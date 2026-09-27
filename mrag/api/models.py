from typing import Any

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str
    profile: str | None = None
    # Omit to use the resolved profile's retrieval.top_k.
    top_k: int | None = Field(default=None, ge=1, le=100)
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
    reranked: bool
    results: list[ChunkResult]


class DocumentItem(BaseModel):
    """One row of the shared document inventory (SPEC-CLI-006).

    `status` is the stored extraction value; `aggregate_status` is the status
    the `status` query parameter filters on.
    """

    document_id: str
    display_name: str
    source_identity: str
    source_binding_status: str
    content_hash: str | None
    status: str
    aggregate_status: str
    source_status: str
    index_status: str
    retrieval_status: str
    profile: str
    exclusion_id: str | None
    created_at: str
    updated_at: str
    ingest_ms: int | None
    id: str
    filename: str
    file_hash: str
    source_type: str


class DocumentListFilter(BaseModel):
    all: bool
    statuses: list[str]


class DocumentListPage(BaseModel):
    limit: int
    offset: int
    count: int
    next_offset: int | None


class DocumentListResponse(BaseModel):
    schema_version: int
    status: str
    profile: str
    filter: DocumentListFilter
    total: int = Field(description="Documents the visibility rules admit, before the status filter.")
    returned: int = Field(description="Documents left after the status filter, across every page.")
    page: DocumentListPage
    documents: list[DocumentItem]


class DocumentDetail(BaseModel):
    id: str
    filename: str
    file_hash: str
    status: str
    created_at: str
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
