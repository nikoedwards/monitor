#!/bin/bash
set -euo pipefail
if [[ ${SSH_ORIGINAL_COMMAND:-} =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
  exec sudo -n /usr/local/sbin/deploy-monitor "${BASH_REMATCH[1]}"
fi
echo 'This key only accepts Monitor deployment requests.' >&2
exit 64
