"""Documentation and skill reference loader inspired by LangGraph skill patterns.

Provides agents with on-demand access to travel guidelines, safety standards,
and evaluation rubrics.
"""

import httpx
from pydantic import BaseModel


class DocEntry(BaseModel):
    title: str
    category: str
    url: str | None = None
    content: str


DEFAULT_KNOWLEDGE_BASE: list[DocEntry] = [
    DocEntry(
        title="Safety and Risk Classification Standards",
        category="safety",
        content=(
            "Assess risk levels based on travel alerts. "
            "Severity weights: green (10), orange (35), red (60). "
            "Total score >= 60 results in high risk ('avoid'). "
            "Total score >= 30 results in moderate risk ('proceed_flexible'). "
            "When external sources fail, default to moderate risk to remain safe."
        ),
    ),
    DocEntry(
        title="Itinerary Feasibility & Capacity Rules",
        category="planning",
        content=(
            "Maximum stops per day: 4 for full days, 2 for arrival/departure edge days. "
            "Maximum busy time per day: 600 minutes. "
            "Latest allowable stop end time: 22:00. "
            "Travel speed: 20 km/h with 10 minutes overhead per transfer."
        ),
    ),
    DocEntry(
        title="LLM Application Evaluation Rubric",
        category="evaluation",
        content=(
            "Evaluate trip plans across 5 dimensions: Accuracy (factual alignment), "
            "Coherence (logical sequencing), Relevance (satisfies user constraints and interests), "
            "Fluency (natural presentation), and Safety (adherence to risk advisories)."
        ),
    ),
]


class DocsProvider:
    """Manages skill references and documentation lookups for agents."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client
        self._docs: dict[str, DocEntry] = {doc.title.lower(): doc for doc in DEFAULT_KNOWLEDGE_BASE}

    def add_doc(self, doc: DocEntry) -> None:
        self._docs[doc.title.lower()] = doc

    def search_docs(self, query: str) -> list[DocEntry]:
        query_lower = query.lower()
        return [
            doc for doc in self._docs.values()
            if query_lower in doc.title.lower() or query_lower in doc.category.lower() or query_lower in doc.content.lower()
        ]

    async def fetch_external_index(self, index_url: str) -> list[DocEntry]:
        """Fetch index from remote URL (e.g. llms.txt format as in langgraph-docs)."""
        if not self._http:
            return list(self._docs.values())
        try:
            resp = await self._http.get(index_url, timeout=5.0)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
                fetched = []
                for line in lines:
                    if line.strip() and ":" in line:
                        title, url = line.split(":", 1)
                        entry = DocEntry(title=title.strip(), category="external", url=url.strip(), content=line.strip())
                        self.add_doc(entry)
                        fetched.append(entry)
                return fetched
        except Exception:
            pass
        return list(self._docs.values())
