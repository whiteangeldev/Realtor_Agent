from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RawSourcePage:
    """One raw response page from a data source."""

    source: str
    endpoint: str
    query_params: dict[str, Any]
    raw_json: dict[str, Any]


class SourceAdapter(Protocol):
    """Common shape for every data source adapter."""

    source: str

    def fetch_page(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 10,
    ) -> RawSourcePage:
        """Fetch one raw page from the source."""
        ...

    def fetch_all(
        self,
        query: str = "",
        hits_per_page: int = 1000,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        """Fetch all raw pages from the source."""
        ...

    def fetch_pages(
        self,
        query: str = "",
        hits_per_page: int = 1000,
        max_pages: int | None = None,
    ) -> Iterator[RawSourcePage]:
        """Yield raw source pages one by one."""
        ...
