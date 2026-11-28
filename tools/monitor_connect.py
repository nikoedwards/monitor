"""Entry point for the portable Monitor connector (bundled as monitor.pyz)."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit

import monitor_cli
import monitor_mcp


def instance(url):
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("请输入 Monitor 页面上的 HTTP 或 HTTPS 服务地址。")
    url = url.rstrip("/")
    key = hashlib.sha256(url.encode()).hexdigest()[:12]
    return url, Path.home() / ".monitorhub" / key


def atomic_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        name = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
        except BaseException:
            temp.close()
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def activate(folder):
    settings = json.loads((folder / "connection.json").read_text(encoding="utf-8"))
    os.environ["MONITOR_BASE_URL"] = settings["url"]
    os.environ["MONITOR_ACCESS_CODE"] = settings["code"]


def authenticate(url):
    os.environ.update(MONITOR_BASE_URL=url, MONITOR_ACCESS_CODE="")
    try:
        overview = monitor_cli.fetch("/api/integrations/overview")
    except RuntimeError as error:
        if not isinstance(error.__cause__, HTTPError) or error.__cause__.code != 401:
            raise
        print("此实例需要口令。请输入打开 Monitor 网站时使用的访问口令。")
        if url.startswith("http://") and urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            print("此地址使用明文 HTTP。请确认网络可信，或按 Ctrl+C 后改用 HTTPS 或隧道。")
        os.environ["MONITOR_ACCESS_CODE"] = getpass.getpass("Monitor 访问口令（输入时隐藏）: ").strip()
        try:
            overview = monitor_cli.fetch("/api/integrations/overview")
        except RuntimeError as retry:
            if isinstance(retry.__cause__, HTTPError) and retry.__cause__.code == 401:
                raise ValueError("口令未通过。请检查口令，再运行同一条连接命令。") from retry
            raise
    if overview.get("read_only") is not True or not isinstance(overview.get("counts", {}).get("brands"), int):
        raise ValueError("该地址没有返回 Monitor 数据概览，请检查服务地址。")
    return overview["counts"]


def desktop_config():
    if sys.platform == "win32":
        conventional = Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
        packages = Path(os.environ["LOCALAPPDATA"]) / "Packages"
        candidates = sorted(packages.glob("Claude_*/LocalCache/Roaming/Claude"))
        if not conventional.is_file() and len(candidates) == 1:
            return candidates[0] / conventional.name
        return conventional
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Claude/claude_desktop_config.json"


def client_program(program):
    executable = shutil.which(program)
    if executable or program != "codex":
        return executable
    if sys.platform == "win32":
        bins = Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/bin"
        installed = sorted(bins.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if installed:
            return str(installed[0])
    if sys.platform == "darwin":
        for apps in [Path("/Applications"), Path.home() / "Applications"]:
            binary = apps / "Codex.app/Contents/Resources/codex"
            if binary.is_file():
                return str(binary)
    return None


def register(client, folder):
    name = "monitor-" + folder.name
    command = sys.executable
    args = [str(folder / "monitor.pyz"), "serve"]
    if client == "desktop":
        dest = desktop_config()
        settings = json.loads(dest.read_text(encoding="utf-8-sig")) if dest.exists() else {}
        servers = settings.setdefault("mcpServers", {})
        entry = {"command": command, "args": args}
        if name in servers and servers[name] != entry:
            raise ValueError(f"{dest} 中已有不同的同名服务 {name}。请检查后重试。")
        servers[name] = entry
        atomic_write(dest, (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode())
        return
    program = "codex" if client == "codex" else "claude"
    executable = client_program(program)
    if not executable:
        raise ValueError(f"找不到 {program} 命令。请安装 {program} CLI，并在能运行 {program} --version 的终端重试。")
    if client == "claude":
        current = subprocess.run([executable, "mcp", "get", name], capture_output=True, text=True, errors="replace")
        if current.returncode == 0 and str(folder / "monitor.pyz") in current.stdout and command in current.stdout:
            return
        flags = ["--scope", "user"]
    else:
        flags = []
    subprocess.run([executable, "mcp", "add", *flags, name, "--", command, *args], check=True)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("connect")
    setup.add_argument("client", choices=["codex", "desktop", "claude", "cli"])
    setup.add_argument("url")
    query = commands.add_parser("query")
    query.add_argument("url")
    query.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("serve")
    args = parser.parse_args()
    if args.action == "serve":
        activate(Path(sys.argv[0]).resolve().parent)
        return monitor_mcp.main()
    url, folder = instance(args.url)
    if args.action == "query":
        if not (folder / "connection.json").is_file():
            raise ValueError("请先执行页面第 2 步的连接命令。")
        activate(folder)
        return monitor_cli.main(args.arguments)
    counts = authenticate(url)
    image = Path(sys.argv[0]).resolve()
    atomic_write(folder / "monitor.pyz", image.read_bytes())
    if args.client != "cli":
        register(args.client, folder)
    atomic_write(folder / "connection.json", json.dumps({"url": url, "code": os.environ["MONITOR_ACCESS_CODE"]}).encode())
    print(f"连接成功。Monitor 中有 {counts['brands']} 个品牌、{counts['records']} 条记录。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError, EOFError) as exc:
        print(f"连接未完成。{exc}", file=sys.stderr)
        sys.exit(1)
