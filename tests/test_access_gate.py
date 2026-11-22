import unittest

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from server.access_gate import AccessGate, COOKIE_NAME


CODE = "test-friend-code"


def client_for(code=CODE):
    async def health(_):
        return JSONResponse({"ok": True})

    async def content(_):
        return PlainTextResponse("private data", headers={"Cache-Control": "public, max-age=86400"})

    app = Starlette(routes=[
        Route("/api/health", health),
        Route("/{path:path}", content, methods=["GET", "POST"]),
    ])
    app.add_middleware(AccessGate, code=code)
    return TestClient(app, base_url="https://monitor.test")


class AccessGateTests(unittest.TestCase):
    def test_anonymous_traffic_is_blocked_before_site_routes(self):
        with client_for() as client:
            gate = client.get("/")
            self.assertEqual(gate.status_code, 401)
            self.assertIn('action="/_access"', gate.text)
            for path in ("/api/brands", "/assets/app.js", "/snapshots/archive.html"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertNotIn("private data", response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(client.post("/api/health").status_code, 401)
            self.assertEqual(client.post("/api/brands").status_code, 401)
            self.assertEqual(client.get("/api/health").json(), {"ok": True})

    def test_correct_code_sets_browser_cookie_and_rotation_invalidates_it(self):
        with client_for() as client:
            wrong = client.post("/_access", data={"code": "other"})
            self.assertEqual(wrong.status_code, 401)
            self.assertNotIn("set-cookie", wrong.headers)
            success = client.post("/_access", data={"code": CODE}, follow_redirects=False)
            self.assertEqual(success.status_code, 303)
            self.assertEqual(success.headers["location"], "/")
            cookie_header = success.headers["set-cookie"].lower()
            for flag in ("httponly", "secure", "samesite=lax", "max-age=2592000"):
                self.assertIn(flag, cookie_header)
            response = client.get("/snapshots/archive.html")
            self.assertEqual(response.text, "private data")
            self.assertEqual(response.headers["cache-control"], "private, no-cache")
            session = client.cookies[COOKIE_NAME]

        with client_for("test-rotated-code") as client:
            client.cookies.set(COOKIE_NAME, session)
            self.assertEqual(client.get("/").status_code, 401)

    def test_bearer_code_supports_authenticated_machine_clients(self):
        with client_for() as client:
            headers = {"Authorization": f"Bearer {CODE}"}
            self.assertEqual(client.get("/api/brands", headers=headers).text, "private data")
