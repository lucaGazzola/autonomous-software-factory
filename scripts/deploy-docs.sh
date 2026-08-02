#!/usr/bin/env bash
# Builds the MkDocs site and deploys it to /var/www/forgeo, served by
# nginx on LAN port 8080. TLS is terminated by Nginx Proxy Manager on
# the owner's LAN, which proxies forgeo.org (via Cloudflare) to this
# backend. Needs root (for /var/www, /etc/nginx, and the nginx reload):
#   sudo scripts/deploy-docs.sh
# Idempotent: re-running refreshes the site and syncs the nginx vhost.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
doc_root=/var/www/forgeo
nginx_avail=/etc/nginx/sites-available/forgeo
nginx_enabled=/etc/nginx/sites-enabled/forgeo
python="${PYTHON:-$repo_dir/.venv/bin/python}"

cd "$repo_dir"

# 1. Build the site warning-free.
"$python" -m mkdocs build --strict

# 2. Deploy to the nginx doc root (drop stale paths from earlier setups).
rm -rf /var/www/casasovere /etc/cloudflare/forgeo /etc/letsencrypt/live/forgeo.org
install -d "$doc_root"
cp -a site/. "$doc_root/"
chown -R www-data:www-data "$doc_root"

# 3. Sync the nginx vhost.
install -m 644 config/nginx-forgeo.conf "$nginx_avail"
ln -sf "$nginx_avail" "$nginx_enabled"

# 4. Validate and reload.
nginx -t
systemctl reload nginx

echo "Docs backend deployed on LAN port 8080 (NPM fronts it at https://forgeo.org/)"
