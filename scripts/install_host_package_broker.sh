#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/atlas_package_broker.py"
DEST="/usr/local/libexec/atlas-package-broker"
UNIT="/etc/systemd/system/atlas-package-broker.service"
SOCKET_PATH="/run/atlas-package-broker/control.sock"
SERVICE_USER=${ATLAS_SERVICE_USER:-atlas}

case "$SERVICE_USER" in
  *[!A-Za-z0-9._-]*|'')
    echo "invalid Atlas service user" >&2
    exit 2
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "run this installer with sudo" >&2
  exit 1
fi

SERVICE_UID=$(id -u "$SERVICE_USER")
SERVICE_GID=$(id -g "$SERVICE_USER")
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$SOURCE" "$DEST"
cat > "$UNIT" <<EOF
[Unit]
Description=Atlas narrow privileged package broker
After=network-online.target

[Service]
Type=simple
ExecStart=$DEST --socket $SOCKET_PATH --allowed-uid $SERVICE_UID --socket-gid $SERVICE_GID
Restart=on-failure
RestartSec=2
RuntimeDirectory=atlas-package-broker
RuntimeDirectoryMode=0750
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now atlas-package-broker.service
systemctl --no-pager --full status atlas-package-broker.service
