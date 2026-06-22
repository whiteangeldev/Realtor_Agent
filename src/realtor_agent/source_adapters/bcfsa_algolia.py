import os
from pathlib import Path
from typing import Any

import httpx

from realtor_agent.source_adapters.base import RawSourcePage

MAX_HITS_PER_PAGE = 1000
ENV_FILE = Path(".env")
REQUIRED_SETTINGS = (
    "BCFSA_ALGOLIA_APP_ID",
    "BCFSA_ALGOLIA_API_KEY",
    "BCFSA_ALGOLIA_INDEX",
    "BCFSA_ALGOLIA_FILTERS",
)


class BCFSAAlgoliaAdapter:
    """Source adapter for BCFSA's Algolia-backed realtor search."""

    source = "BCFSA"

    def __init__(self) -> None:
        settings = _load_settings()
        self.app_id = settings["BCFSA_ALGOLIA_APP_ID"]
        self.api_key = settings["BCFSA_ALGOLIA_API_KEY"]
        self.index_name = settings["BCFSA_ALGOLIA_INDEX"]
        self.filters = settings["BCFSA_ALGOLIA_FILTERS"]

    @property
    def endpoint(self) -> str:
        return f"https://{self.app_id}-dsn.algolia.net/1/indexes/{self.index_name}/query"

    def fetch_page(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 10,
    ) -> RawSourcePage:
        payload = self._query_params(query=query, page=page, hits_per_page=hits_per_page)

        headers = {
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.api_key,
        }

        response = httpx.post(self.endpoint, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return RawSourcePage(
            source=self.source,
            endpoint=self.endpoint,
            query_params=payload,
            raw_json=response.json(),
        )

    def fetch_pages(
        self,
        query: str = "",
        hits_per_page: int = 1000,
        max_pages: int | None = None,
    ):
        first_page = self.fetch_page(query=query, page=0, hits_per_page=hits_per_page)
        yield first_page

        total_pages = int(first_page.raw_json.get("nbPages", 0))
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        for page in range(1, total_pages):
            yield self.fetch_page(query=query, page=page, hits_per_page=hits_per_page)

    def fetch_all(
        self,
        query: str = "",
        hits_per_page: int = 1000,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        pages = list(
            self.fetch_pages(query=query, hits_per_page=hits_per_page, max_pages=max_pages)
        )
        first_json = pages[0].raw_json if pages else {}
        all_hits = []
        for page in pages:
            all_hits.extend(page.raw_json.get("hits", []))

        return {
            "source": self.source,
            "hits": all_hits,
            "nbHits": first_json.get("nbHits"),
            "nbPages": first_json.get("nbPages"),
            "hitsPerPage": first_json.get("hitsPerPage"),
            "fetchedPages": len(pages),
        }

    def _query_params(self, query: str, page: int, hits_per_page: int) -> dict[str, Any]:
        return {
            "query": query,
            "page": page,
            "hitsPerPage": max(1, min(hits_per_page, MAX_HITS_PER_PAGE)),
            "filters": self.filters,
        }


def _load_settings() -> dict[str, str]:
    settings = _read_env_file(ENV_FILE)
    settings.update({key: value for key in REQUIRED_SETTINGS if (value := os.getenv(key))})

    missing = [key for key in REQUIRED_SETTINGS if not settings.get(key)]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Missing required BCFSA settings: {names}")

    return settings


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    settings: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip().strip("\"'")
    return settings
