#!/usr/bin/env bash
set -euo pipefail

readonly SERVER_IP="10.10.9.80"
readonly CERT_DIR="/etc/nginx/ssl"
readonly CERT_PATH="${CERT_DIR}/10.10.9.80.crt"
readonly KEY_PATH="${CERT_DIR}/10.10.9.80.key"
readonly NGINX_CONFIG="/etc/nginx/nginx.conf"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

pacman -S --needed --noconfirm nginx openssl

if [[ -f "${CERT_PATH}" || -f "${KEY_PATH}" ]]; then
  printf 'Certificate or key already exists; refusing to overwrite it: %s\n' "${CERT_DIR}" >&2
  exit 1
fi

install -d -m 0755 "${CERT_DIR}"
openssl req -x509 -newkey rsa:4096 -sha256 -nodes -days 365 \
  -keyout "${KEY_PATH}" \
  -out "${CERT_PATH}" \
  -subj "/CN=${SERVER_IP}" \
  -addext "subjectAltName = IP:${SERVER_IP}"
chmod 0600 "${KEY_PATH}"
chmod 0644 "${CERT_PATH}"

if [[ -f "${NGINX_CONFIG}" ]]; then
  cp --preserve=mode,ownership,timestamps "${NGINX_CONFIG}" "${NGINX_CONFIG}.bak-$(date +%Y%m%d%H%M%S)"
fi

cat >"${NGINX_CONFIG}" <<EOF
user http;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                    '\$status \$body_bytes_sent "\$http_referer" '
                    '"\$http_user_agent" "\$http_x_forwarded_for"';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    keepalive_timeout 65;

    map \$http_upgrade \$connection_upgrade {
        default upgrade;
        '' close;
    }

    server {
        listen 80;
        listen [::]:80;
        server_name ${SERVER_IP};
        return 301 https://\$host\$request_uri;
    }

    server {
        listen 443 ssl;
        listen [::]:443 ssl;
        server_name ${SERVER_IP};

        ssl_certificate ${CERT_PATH};
        ssl_certificate_key ${KEY_PATH};
        ssl_protocols TLSv1.2 TLSv1.3;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;
        }
    }
}
EOF

nginx -t
systemctl enable --now nginx

printf '\nNGINX is active. HTTP redirects to HTTPS; HTTPS proxies to 127.0.0.1:8000.\n'
