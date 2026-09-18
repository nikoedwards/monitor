# VPS 部署

这套配置适用于腾讯云新加坡、阿里云新加坡/香港等可以稳定访问项目数据源的 Linux VPS。应用和 Caddy 都在同一个 Docker Compose 网络中，SQLite、网页截图和 HTML 归档绑定到仓库下的 `data/` 目录。

## 首次部署

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-plugin
sudo systemctl enable --now docker

git clone <仓库地址> monitor
cd monitor
cp .env.example .env
# 编辑 .env，填写需要的 API 凭证和 DOMAIN
mkdir -p data backups

# 仅启动应用（可用 SSH 隧道访问 8790）
docker compose up -d --build
curl http://127.0.0.1:8790/api/health

# 有域名并已把 DNS 指向本机时，启用自动 HTTPS
docker compose --profile proxy up -d --build
```

防火墙只需开放 SSH；启用 Caddy 时再开放 TCP 80/443 和 UDP 443。应用端口默认只绑定到 `127.0.0.1`，避免绕过 Caddy 直接暴露。

## 备份与恢复

备份脚本会短暂停止应用，打包 SQLite（包括 WAL 文件）、PNG 和 HTML 快照，并默认保留 14 天：

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh
```

可把脚本加入 root 的 cron，例如每天凌晨 03:15 执行：

```cron
15 3 * * * cd /opt/monitor && /opt/monitor/deploy/backup.sh >> /var/log/monitor-backup.log 2>&1
```

恢复时先停止应用，再在仓库根目录解压备份：

```bash
docker compose stop monitor
tar -xzf backups/monitor-data-YYYYMMDDTHHMMSSZ.tar.gz
docker compose up -d monitor
```

不要把 `.env` 或 `data/` 提交到 Git；`.env.example` 只包含变量名和空值。
