"""Build the downloadable connector reproducibly from the shipped CLI and MCP."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]


def archive(files):
    stream = BytesIO()
    with ZipFile(stream, "w") as output:
        for name, data in files.items():
            member = ZipInfo(name)
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            output.writestr(member, data, compress_type=ZIP_DEFLATED)
    return stream.getvalue()


def source(name):
    return (ROOT / "tools" / name).read_text(encoding="utf-8").encode("utf-8")


def build():
    program = archive({"__main__.py": source("monitor_connect.py"), "monitor_cli.py": source("monitor_cli.py"), "monitor_mcp.py": source("monitor_mcp.py")})
    return archive({"monitor.pyz": program, "monitor.cmd": source("monitor.cmd").replace(b"\n", b"\r\n")})


if __name__ == "__main__":
    dest = ROOT / "downloads" / "monitor-connect-v1.zip"
    dest.parent.mkdir(exist_ok=True)
    dest.write_bytes(build())
