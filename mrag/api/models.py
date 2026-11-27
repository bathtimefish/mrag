from typing import Any

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str
    profile: str | None = None
    # Omit to use the resolved profile's retrieval.top_k.
    top_k: int | None = Field(default=None, ge=1, le=100)
    strategy: str | None = None  # hybrid | vector | keyword | parent_child


class SearchLocation(BaseModel):
    """Which extraction a chunk was cut from, and where in it."""

    source_format: str | None = Field(description="`markdown` or `text`: the extraction the chunk was cut from.")
    source_start: int | None = Field(description="Character offset of the start; chunks do not record it, so null.")
    source_end: int | None = Field(description="Character offset of the end; chunks do not record it, so null.")


class SearchReference(BaseModel):
    """Where a result's text came from, for a citation.

    Every field is always present; a value that does not apply is null. Paths
    are project-relative.
    """

    display_name: str = Field(description="The document's name, as the document list shows it.")
    source_binding_status: str = Field(description="`project_relative`, `external_root` or `legacy_unbound`.")
    source_path: str | None = Field(
        description="The source file, project-relative, when it lives in the project; null otherwise."
    )
    original: str | None = Field(description="The stored original, project-relative.")
    extracted_markdown: str | None = Field(description="The Markdown extraction, project-relative.")
    resource: str = Field(description="The MCP resource that serves the stored original.")
    revision_id: str | None = Field(description="Always null: a document keeps one extraction.")
    location: SearchLocation
    page_start: int | None = Field(description="The page of the original the text begins on, counted from 1; not the printed number. Null without pages on record (exp/html-structure).")
    page_end: int | None = Field(description="The page it ends on.")
    page_label_start: str | None = Field(description="The page number printed on page_start: what a citation names.")
    page_label_end: str | None = Field(description="The page number printed on page_end.")


class ChunkResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    score: float
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    reference: SearchReference
    structured_content: str | None = Field(default=None, description="The text with every table row written out under its headers; null when it holds no such row, and for a document without a structure record (exp/html-structure).")
    criteria: list[dict[str, Any]] = Field(default_factory=list, description="The values the document marks as criteria on the result's lines, at most 15, each with its limit, unit, quantity, the labels a table puts it under, the heading above it and its page.")
    criteria_omitted: int = Field(default=0, description="Criterion values the result holds beyond the 15 given.")


class RetrieveResponse(BaseModel):
    query: str
    profile: str
    strategy: str
    reranked: bool
    results: list[ChunkResult]


class DocumentItem(BaseModel):
    """One row of the document inventory.

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
