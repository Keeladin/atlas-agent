#!/bin/sh
set -eu
KEY_SOURCE="/home/jaco/Projects/atlas-agent-state/production/secrets/android-phone-ssh-key"
KNOWN_HOSTS="/home/jaco/Projects/atlas-agent-state/production/secrets/android-phone-known-hosts"
TMP_KEY="$(mktemp)"
trap 'rm -f "$TMP_KEY"' EXIT HUP INT TERM
umask 077
cp "$KEY_SOURCE" "$TMP_KEY"
chmod 600 "$TMP_KEY"
cd /home/jaco/Projects/atlas-agent
/home/jaco/Projects/atlas-agent/.venv/bin/python -m atlas_providers.android_phone_mcp \
  --ssh-host 100.93.39.106 --ssh-user u0_a382 --ssh-port 8022 \
  --ssh-key "$TMP_KEY" --ssh-known-hosts "$KNOWN_HOSTS" \
  --remote-bridge /data/data/com.termux/files/home/.atlas/android_phone_bridge.py
