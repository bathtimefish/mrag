from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class QdrantConfig(BaseModel):
    mode: str = "server"
    host: str = "localhost"
    port: int = 6333


class KnowledgeBaseConfig(BaseModel):
    id: str
    name: str


class ExtractionPdfConfig(BaseModel):
    provider: str = "pymupdf"
    output_format: str = "text"


class DefaultExtractionConfig(BaseModel):
    pdf: ExtractionPdfConfig = Field(default_factory=ExtractionPdfConfig)


class ProjectSection(BaseModel):
    name: str


class ProjectConfig(BaseModel):
    project: ProjectSection
    knowledge_base: KnowledgeBaseConfig
    default_profile: str = "default"
    fts_tokenizer: str = "trigram"
    default_extraction: DefaultExtractionConfig = Field(
        default_factory=DefaultExtractionConfig
    )
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)

    @property
    def knowledge_id(self) -> str:
        return self.knowledge_base.id


def load_project_config(project_dir: Path | None = None) -> ProjectConfig:
    if project_dir is None:
        project_dir = Path.cwd()

    mrag_yaml = project_dir / "mrag.yaml"
    if not mrag_yaml.exists():
        raise FileNotFoundError(
            f"mrag.yaml not found in {project_dir}. Run 'mrag init' to initialize a project."
        )

    data = yaml.safe_load(mrag_yaml.read_text(encoding="utf-8"))
    return ProjectConfig(**data)
