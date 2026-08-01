#!/usr/bin/env bash
# Patch Keycloak clients + hostname for PUBLIC_HOST access.
set -euo pipefail

PUBLIC_HOST="${PUBLIC_HOST:-10.2.67.21}"
KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${REALM:-reports-realm}"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" -d "username=admin" -d "password=admin" \
  -d "grant_type=password" | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

patch_client() {
  local client_id="$1"
  local cid
  cid=$(curl -sf "${AUTH[@]}" \
    "$KEYCLOAK_URL/admin/realms/$REALM/clients?clientId=$client_id" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

  curl -sf "${AUTH[@]}" \
    "$KEYCLOAK_URL/admin/realms/$REALM/clients/$cid" \
    | python3 -c "
import sys, json
c = json.load(sys.stdin)
host = '''$PUBLIC_HOST'''
if c.get('clientId') == 'bionicpro-auth':
    c['redirectUris'] = [
        f'http://{host}:8001/auth/callback',
        'http://localhost:8001/auth/callback',
    ]
    c['webOrigins'] = [
        f'http://{host}:8001', f'http://{host}:3000',
        'http://localhost:8001', 'http://localhost:3000',
    ]
elif c.get('clientId') == 'reports-frontend':
    c['redirectUris'] = [f'http://{host}:3000/*', 'http://localhost:3000/*']
    c['webOrigins'] = [f'http://{host}:3000', 'http://localhost:3000']
print(json.dumps(c))
" > /tmp/kc-client.json

  curl -sf "${AUTH[@]}" -X PUT \
    "$KEYCLOAK_URL/admin/realms/$REALM/clients/$cid" \
    -d @/tmp/kc-client.json >/dev/null
  echo "patched client $client_id"
}

patch_client bionicpro-auth
patch_client reports-frontend
echo "Done. Use http://$PUBLIC_HOST:3000"
