"""Small dependency-free command line client for MonitorHub's read-only API.

Examples::

    python -m tools.monitor_cli overview
    python -m tools.monitor_cli brands --json
    python -m tools.monitor_cli records --brand-id BRAND --days 7
    python -m tools.monitor_cli query records --q battery --limit 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _base_url(value: str | None = None) -> str:
    return (value or os.environ.get("MONITOR_BASE_URL") or "http://127.0.0.1:8790").rstrip("/")


def fetch(path: str, params: dict[str, object] | None = None, *, base_url: str | None = None) -> dict:
    query = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    url = f"{_base_url(base_url)}{path}"
    if query:
        url += "?" + urlencode(query)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "monitorhub-cli/1"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "read", lambda: b"")()
        try:
            detail = json.loads(detail.decode("utf-8")).get("detail", "")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"MonitorHub request failed: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("MonitorHub returned a non-object response")
    return payload


def _params(args: argparse.Namespace) -> dict[str, object]:
    return {key: getattr(args, key, None) for key in ("brand_id", "q", "start_date", "end_date", "days", "limit")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitorhub", description="Read-only MonitorHub query client")
    parser.add_argument("--base-url", default=None, help="MonitorHub URL (default: MONITOR_BASE_URL or localhost)")
    parser.add_argument("--json", action="store_true", dest="global_json", help="print JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("overview", "brands"):
        sub_parser = sub.add_parser(command)
        sub_parser.add_argument("--json", action="store_true", dest="as_json", help="print JSON")
    records = sub.add_parser("records", help="query unified content records")
    records.add_argument("--json", action="store_true", dest="as_json", help="print JSON")
    records.add_argument("--brand-id"); records.add_argument("--q"); records.add_argument("--days", type=int)
    records.add_argument("--start-date"); records.add_argument("--end-date"); records.add_argument("--limit", type=int, default=50)
    query = sub.add_parser("query", help="query one integration dataset")
    query.add_argument("--json", action="store_true", dest="as_json", help="print JSON")
    query.add_argument("dataset", choices=("records", "brands", "products", "links", "sources", "sales_metrics", "job_postings", "web_monitors", "web_snapshots"))
    query.add_argument("--brand-id"); query.add_argument("--q"); query.add_argument("--start-date"); query.add_argument("--end-date"); query.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        if args.command == "overview":
            result = fetch("/api/integrations/overview", base_url=args.base_url)
        elif args.command == "brands":
            result = fetch("/api/integrations/query", {"dataset": "brands", "limit": 200}, base_url=args.base_url)
        elif args.command == "records":
            result = fetch("/api/records", _params(args), base_url=args.base_url)
        else:
            params = {"dataset": args.dataset, **_params(args)}
            result = fetch("/api/integrations/query", params, base_url=args.base_url)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.as_json or args.global_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "rows" in result:
        for row in result["rows"]:
            print(json.dumps(row, ensure_ascii=False))
    elif "records" in result:
        for row in result["records"]:
            print(json.dumps(row, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
