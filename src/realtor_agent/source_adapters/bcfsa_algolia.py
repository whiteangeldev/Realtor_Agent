import os
from pathlib import Path
from typing import Any

import httpx

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

    source_name = "BCFSA"

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
    ) -> dict[str, Any]:
        hits_per_page = max(1, min(hits_per_page, MAX_HITS_PER_PAGE))
        payload = {
            "query": query,
            "page": page,
            "hitsPerPage": hits_per_page,
            "filters": self.filters,
        }

        headers = {
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.api_key,
        }

        response = httpx.post(self.endpoint, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def fetch_all(
        self,
        query: str = "",
        hits_per_page: int = 1000,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        first_page = self.fetch_page(query=query, page=0, hits_per_page=hits_per_page)
        all_hits = list(first_page.get("hits", []))
        total_pages = int(first_page.get("nbPages", 0))

        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        for page in range(1, total_pages):
            page_response = self.fetch_page(
                query=query,
                page=page,
                hits_per_page=hits_per_page,
            )
            all_hits.extend(page_response.get("hits", []))

        return {
            "source": self.source_name,
            "hits": all_hits,
            "nbHits": first_page.get("nbHits"),
            "nbPages": first_page.get("nbPages"),
            "hitsPerPage": first_page.get("hitsPerPage"),
            "fetchedPages": total_pages,
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
