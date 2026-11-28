"""Enable IP HTTPS on the existing Monitor Caddy gateway, keeping its HTTP site."""
from pathlib import Path
import shutil
import subprocess


IP = "47.79.224.196"
CONTAINER = "monitor-caddy-1"
SITE = f"""
https://{IP} {{
    tls {{
        issuer acme {{
            dir https://acme-v02.api.letsencrypt.org/directory
            profile shortlived
        }}
    }}
    reverse_proxy monitor:8790
}}
"""


def configure():
    source = subprocess.check_output([
        "docker", "inspect", "--format",
        '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}',
        CONTAINER,
    ], text=True).strip()
    path = Path(source)
    if not path.is_file():
        raise RuntimeError("Could not locate the existing gateway's mounted Caddyfile")
    original = path.read_bytes()
    current = original.decode()
    if SITE.strip() in current:
        return
    if f"https://{IP}" in current:
        raise RuntimeError("The gateway already has a different HTTPS site for Monitor")
    # IP clients generally omit TLS SNI. On a NATed VPS the local interface
    # address differs from the public IP on the certificate.
    option = f"    default_sni {IP}"
    lines = current.splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")), len(lines))
    if first < len(lines) and lines[first].strip() == "{":
        lines.insert(first + 1, option + "\n")
    else:
        lines.insert(first, "{\n" + option + "\n}\n\n")
    updated = ("".join(lines).rstrip() + "\n" + SITE).encode()
    command = ["docker", "exec", "-i", CONTAINER, "caddy"]
    flags = ["--config", "-", "--adapter", "caddyfile"]
    subprocess.run([*command, "validate", *flags], input=updated, check=True)
    backup = path.with_name(path.name + ".before-mcp-https")
    if not backup.exists():
        shutil.copy2(path, backup)
    try:
        # Preserve the bind-mounted inode so a restart sees the same file.
        path.write_bytes(updated)
        subprocess.run([*command, "reload", *flags], input=updated, check=True)
    except BaseException:
        path.write_bytes(original)
        subprocess.run([*command, "reload", *flags], input=original, check=True)
        raise


if __name__ == "__main__":
    configure()
