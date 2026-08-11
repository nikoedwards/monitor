from __future__ import annotations

import asyncio
import unittest

from starlette.requests import Request
from starlette.responses import Response

from server.app import secure_snapshot_archives


def request_for(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


async def empty_response(_request: Request) -> Response:
    return Response()


class HttpCacheTests(unittest.TestCase):
    def test_dynamic_api_responses_are_not_cached(self):
        response = asyncio.run(secure_snapshot_archives(request_for("/api/web/snapshots"), empty_response))

        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")

    def test_snapshot_comparison_keeps_immutable_cache_header(self):
        async def comparison_response(_request: Request) -> Response:
            return Response(headers={"Cache-Control": "public, max-age=31536000, immutable"})

        response = asyncio.run(
            secure_snapshot_archives(request_for("/api/web/snapshots/example/comparison"), comparison_response)
        )

        self.assertEqual(response.headers["cache-control"], "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
