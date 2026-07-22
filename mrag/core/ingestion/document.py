import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mrag.config.project import ProjectConfig
from mrag.db.connection import db_connection, find_db
from mrag.extractors import detect_source_type, get_extractor
from mrag.extractors.base import ExtractionResult


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_duplicate(file_hash: str, db_path: Path) -> str | None:
    """Return existing document_id if a document with this file_hash exists, else None."""
    from mrag.db.connection import open_connection

    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


class DuplicateDocumentError(FileExistsError):
    """A content-identical document is already registered."""

    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__(
            f"Document already exists (id={document_id}). Use --force to re-add."
        )


@dataclass(frozen=True)
class PreparedDocument:
    file_path: Path
    file_hash: str
    source_type: str
    provider: str | None
    extraction: ExtractionResult


def hash_document(file_path: Path) -> str:
    return _sha256(file_path.read_bytes())


def prepare_document(
    file_path: Path,
    config: ProjectConfig,
    extractor_override: str | None = None,
    file_hash: str | None = None,
) -> PreparedDocument:
    """Read and extract one source without changing project state."""
    source_type = detect_source_type(file_path)
    provider = extractor_override or (
        config.default_extraction.pdf.provider if source_type == "pdf" else None
    )
    extractor = get_extractor(source_type, provider)
    return PreparedDocument(
        file_path=file_path,
        file_hash=file_hash or hash_document(file_path),
        source_type=source_type,
        provider=provider,
        extraction=extractor.extract(file_path),
    )


def add_document(
    file_path: Path,
    project_dir: Path,
    config: ProjectConfig,
    extractor_override: str | None = None,
    force: bool = False,
) -> tuple[str, list[str]]:
    """
    Ingest a document into the project.

    Returns (document_id, warnings).
    Raises FileExistsError if the same file_hash is already registered and force=False.
    """
    db_path = find_db(project_dir)
    file_hash = hash_document(file_path)

    existing_id = find_duplicate(file_hash, db_path)
    if existing_id and not force:
        raise DuplicateDocumentError(existing_id)
    prepared = prepare_document(
        file_path,
        config,
        extractor_override,
        file_hash=file_hash,
    )
    return persist_prepared_document(
        prepared,
        project_dir,
        config,
        existing_id=existing_id,
        force=force,
    )


def persist_prepared_document(
    prepared: PreparedDocument,
    project_dir: Path,
    config: ProjectConfig,
    *,
    existing_id: str | None = None,
    force: bool = False,
) -> tuple[str, list[str]]:
    """Persist a prepared extraction; callers serialize this write boundary."""
    db_path = find_db(project_dir)
    file_path = prepared.file_path
    file_hash = prepared.file_hash
    source_type = prepared.source_type
    provider = prepared.provider
    result = prepared.extraction
    registered_id = find_duplicate(file_hash, db_path)
    if registered_id and not force:
        raise DuplicateDocumentError(registered_id)
    if registered_id:
        existing_id = registered_id

    # Reuse existing_id on force so INSERT OR REPLACE hits the PK and replaces the row.
    document_id = existing_id if (existing_id and force) else str(uuid.uuid4())
    doc_dir = project_dir / "data" / "documents" / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Save original file
    original_dest = doc_dir / f"original{file_path.suffix}"
    shutil.copy2(file_path, original_dest)

    # Save extracted files — always both
    md_path = doc_dir / "extracted.md"
    txt_path = doc_dir / "extracted.txt"
    md_path.write_text(result.markdown, encoding="utf-8")
    txt_path.write_text(result.text, encoding="utf-8")

    # extracted_hash = SHA256 of text content (canonical for diff detection)
    extracted_hash = _sha256(result.text)

    # Save extraction metadata
    meta_path = doc_dir / "extraction_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "provider": provider or "plain",
                "output_format": "text" if source_type in ("txt",) else
                                 ("markdown" if source_type == "md" else
                                  config.default_extraction.pdf.output_format),
                "source_type": source_type,
                **result.metadata,
                "warnings": result.warnings,
                "created_at": _now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Relative paths stored in SQLite (relative to project_dir)
    def rel(p: Path) -> str:
        return str(p.relative_to(project_dir))

    now = _now_iso()
    with db_connection(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, knowledge_id, filename, original_path, file_hash, source_type,
                extraction_provider, extraction_output_format,
                extracted_markdown_path, extracted_text_path, extraction_meta_path,
                extracted_hash, status, error_message, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                config.knowledge_id,
                file_path.name,
                rel(original_dest),
                file_hash,
                source_type,
                provider,
                "markdown" if source_type == "md" else "text",
                rel(md_path),
                rel(txt_path),
                rel(meta_path),
                extracted_hash,
                "extracted",
                None,
                now,
                now,
            ),
        )

    return document_id, result.warnings


def get_document(document_id: str, db_path: Path) -> dict | None:
    from mrag.db.connection import open_connection

    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
