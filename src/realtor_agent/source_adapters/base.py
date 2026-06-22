from typing import Any, Protocol


class SourceAdapter(Protocol):
    """Common shape for every data source adapter."""

    source_name: str

    def fetch_page(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 10,
    ) -> dict[str, Any]:
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
