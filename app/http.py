from typing import Any

import httpx

from .errors import RetryableError


async def request_json(http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Perform an HTTP call and return its JSON body, mapping any failure to RetryableError."""
    try:
        response = await http.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RetryableError(f"{method} {url} failed: {exc}") from exc
