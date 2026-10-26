import httpx


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class WebSearchError(RuntimeError):
    pass


def search_web(query: str, api_key: str, result_count: int) -> list[dict[str, str]]:
    try:
        response = httpx.post(
            TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": min(result_count, 20),
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as error:
        raise WebSearchError(
            f"Tavily web search returned HTTP {error.response.status_code}."
        ) from error
    except httpx.HTTPError as error:
        raise WebSearchError("Tavily web search could not be reached.") from error
    except ValueError as error:
        raise WebSearchError("Tavily web search returned an invalid response.") from error

    if not isinstance(payload, dict):
        raise WebSearchError("Tavily web search returned an invalid response.")
    items = payload.get("results", [])
    if not isinstance(items, list):
        raise WebSearchError("Tavily web search returned an invalid results list.")

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("url")
        if not isinstance(link, str) or not link:
            continue
        results.append(
            {
                "title": str(item.get("title", link)),
                "link": link,
                "snippet": str(item.get("content", "")),
            }
        )
    return results
