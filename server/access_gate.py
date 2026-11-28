"""Shared access code for the published Monitor instance."""
import hmac
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse


COOKIE_NAME = "monitor_access"
ACCESS_PATH = "/_access"
SESSION_SECONDS = 30 * 24 * 60 * 60


def access_page(error=""):
    feedback = f'<p class="error" role="alert">{error}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>受邀访问</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      padding: 24px; background: #f5f6f4; color: #202722; font: 16px system-ui, sans-serif; }}
    main {{ width: min(100%, 380px); padding: 32px; background: #fff;
      border: 1px solid #dce2dd; border-radius: 16px; }}
    h1 {{ margin: 0 0 24px; font-size: 24px; }}
    form {{ display: grid; gap: 8px; }}
    input, button {{ width: 100%; padding: 12px; font: inherit; border-radius: 8px; }}
    input {{ border: 1px solid #83928a; }}
    button {{ margin-top: 8px; border: 0; background: #235642; color: #fff; cursor: pointer; }}
    .error {{ margin: 4px 0 0; color: #a12328; font-size: 14px; }}
  </style>
</head>
<body>
  <main>
    <h1>受邀访问</h1>
    <form method="post" action="{ACCESS_PATH}">
      <label for="code">访问口令</label>
      <input id="code" name="code" type="password" autocomplete="current-password"
        autocapitalize="none" maxlength="128" required autofocus>
      {feedback}
      <button type="submit">进入</button>
    </form>
  </main>
</body>
</html>""",
        status_code=401,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


class AccessGate(BaseHTTPMiddleware):
    def __init__(self, app, *, code):
        super().__init__(app)
        self.code = code.encode()
        self.session = hmac.digest(self.code, b"monitor access cookie", "sha256").hex().encode()

    async def dispatch(self, request, call_next):
        path = request.scope["path"]
        if path == "/api/health" and request.method in {"GET", "HEAD"}:
            return await call_next(request)

        if path == ACCESS_PATH and request.method == "POST":
            return await self.unlock(request)

        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        cookie = request.cookies.get(COOKIE_NAME, "").encode()
        if hmac.compare_digest(cookie, self.session) or (
            scheme.lower() == "bearer" and (
                hmac.compare_digest(token.encode("latin-1"), self.code)
                or hmac.compare_digest(token.encode(), self.code)
            )
        ):
            response = await call_next(request)
            if "no-store" not in response.headers.get("cache-control", ""):
                response.headers["Cache-Control"] = "private, no-cache"
            return response

        if request.method in {"GET", "HEAD"} and (
            path == "/" or "text/html" in request.headers.get("accept", "")
        ):
            return access_page()
        return JSONResponse(
            {"detail": "Access code required"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )

    async def unlock(self, request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1024:
                return access_page("口令过长，请重试。")
            body.extend(chunk)
        try:
            code, = parse_qs(body.decode(), max_num_fields=1).get("code", [""])
        except ValueError:
            return access_page("请输入访问口令。")
        if not hmac.compare_digest(code.strip().encode(), self.code):
            return access_page("口令不正确，请重试。")

        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE_NAME,
            self.session.decode(),
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="lax",
            secure=(
                request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
            ),
        )
        return response
