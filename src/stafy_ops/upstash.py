import httpx


class UpstashClient:
    """Minimal Upstash Redis REST client (plain httpx): single commands and pipelines."""

    def __init__(self, url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=10)

    async def cmd(self, *args: str | int):
        resp = await self._client.post(self._url, headers=self._headers, json=list(args))
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Upstash error: {body['error']}")
        return body["result"]

    async def pipeline(self, commands: list[list]) -> list:
        """Runs the commands in one HTTP request; returns their results in order."""
        resp = await self._client.post(f"{self._url}/pipeline", headers=self._headers, json=commands)
        resp.raise_for_status()
        results = resp.json()
        for item in results:
            if "error" in item:
                raise RuntimeError(f"Upstash error: {item['error']}")
        return [item["result"] for item in results]
