from abc import ABC, abstractmethod


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...

    @abstractmethod
    def get_dimension(self) -> int:
        """Return vector dimension. May require at least one embed() call first."""
        ...

    @abstractmethod
    def get_model_id(self) -> str:
        """Return stable version key: provider:model:dimension:revision."""
        ...

    @abstractmethod
    def get_normalized_name(self) -> str:
        """Return slug suitable for Qdrant collection name component."""
        ...
