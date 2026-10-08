#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
: "${DAFTER_ADMIN_TOKEN:?export DAFTER_ADMIN_TOKEN or source the env file first}"
admin=${DAFTER_ADMIN_URL:-http://127.0.0.1:8081}
tenant=${DAFTER_TENANT:-t_9c21a4be}
document=${1:-deploy/aws/tenant.json}

call() {
  curl -fsS -H "Authorization: Bearer ${DAFTER_ADMIN_TOKEN}" -H "X-Dafter-Actor: deploy/aws" "$@"
}

call -X PUT -H 'Content-Type: application/json' --data-binary "@${document}" "${admin}/admin/v1/tenants/${tenant}" >/dev/null
call -X PUT -H 'Content-Type: application/json' --data-binary @deploy/aws/channel-webrtc.json "${admin}/admin/v1/channels/webrtc" >/dev/null
catalog=go/cmd/dafter-control/catalog.json
for language in $(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["languages"]))' "${catalog}"); do
  python3 -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["languages"][sys.argv[2]]))' "${catalog}" "${language}" |
    call -X PUT -H 'Content-Type: application/json' --data-binary @- "${admin}/admin/v1/languages/${language}" >/dev/null
done
call -X POST -H "X-Dafter-Note: deploy/aws tenant defaults from ${document}, the web channel overlay and the catalog's languages" "${admin}/admin/v1/releases"
echo
