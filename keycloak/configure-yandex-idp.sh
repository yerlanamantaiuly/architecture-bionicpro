#!/usr/bin/env bash
# Register Yandex ID as Keycloak Identity Provider (OIDC brokering).
set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${REALM:-reports-realm}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"

# Load secrets (do not commit real values)
if [[ -f "$(dirname "$0")/../secrets/yandex.env" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/../secrets/yandex.env"
fi

YANDEX_CLIENT_ID="${YANDEX_CLIENT_ID:?Set YANDEX_CLIENT_ID}"
YANDEX_CLIENT_SECRET="${YANDEX_CLIENT_SECRET:?Set YANDEX_CLIENT_SECRET}"
ALIAS="yandex"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" -d "username=$ADMIN_USER" -d "password=$ADMIN_PASS" \
  -d "grant_type=password" | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

echo "Delete existing IdP '$ALIAS' if present"
curl -sf "${AUTH[@]}" -X DELETE \
  "$KEYCLOAK_URL/admin/realms/$REALM/identity-provider/instances/$ALIAS" >/dev/null 2>&1 || true

echo "Create Yandex OIDC Identity Provider"
curl -sf "${AUTH[@]}" -X POST \
  "$KEYCLOAK_URL/admin/realms/$REALM/identity-provider/instances" \
  -d "$(python3 - <<PY
import json
print(json.dumps({
  "alias": "$ALIAS",
  "displayName": "Yandex ID",
  "providerId": "oidc",
  "enabled": True,
  "trustEmail": True,
  "storeToken": True,
  "addReadTokenRoleOnCreate": True,
  "linkOnly": False,
  "firstBrokerLoginFlowAlias": "first broker login",
  "config": {
    "authorizationUrl": "https://oauth.yandex.ru/authorize",
    "tokenUrl": "https://oauth.yandex.ru/token",
    "userInfoUrl": "https://login.yandex.ru/info?format=json",
    "clientId": "$YANDEX_CLIENT_ID",
    "clientSecret": "$YANDEX_CLIENT_SECRET",
    "clientAuthMethod": "client_secret_post",
    "syncMode": "IMPORT",
    "defaultScope": "login:info login:email login:avatar",
    "validateSignature": "false",
    "useJwksUrl": "false",
    "pkceEnabled": "false",
    "guiOrder": "1"
  }
}))
PY
)" >/dev/null

echo "Add attribute mappers"
add_mapper() {
  local name="$1"
  local body="$2"
  curl -sf "${AUTH[@]}" -X POST \
    "$KEYCLOAK_URL/admin/realms/$REALM/identity-provider/instances/$ALIAS/mappers" \
    -d "$body" >/dev/null && echo "  mapper: $name"
}

add_mapper "username" '{
  "name": "username",
  "identityProviderAlias": "yandex",
  "identityProviderMapper": "oidc-username-idp-mapper",
  "config": {
    "template": "${CLAIM.login}",
    "syncMode": "INHERIT"
  }
}'

add_mapper "email" '{
  "name": "email",
  "identityProviderAlias": "yandex",
  "identityProviderMapper": "oidc-user-attribute-idp-mapper",
  "config": {
    "claim": "default_email",
    "user.attribute": "email",
    "syncMode": "INHERIT"
  }
}'

add_mapper "firstName" '{
  "name": "firstName",
  "identityProviderAlias": "yandex",
  "identityProviderMapper": "oidc-user-attribute-idp-mapper",
  "config": {
    "claim": "first_name",
    "user.attribute": "firstName",
    "syncMode": "INHERIT"
  }
}'

add_mapper "lastName" '{
  "name": "lastName",
  "identityProviderAlias": "yandex",
  "identityProviderMapper": "oidc-user-attribute-idp-mapper",
  "config": {
    "claim": "last_name",
    "user.attribute": "lastName",
    "syncMode": "INHERIT"
  }
}'

echo
echo "Yandex IdP ready."
echo "Redirect URI in Yandex console MUST be exactly:"
echo "  http://localhost:8080/realms/reports-realm/broker/yandex/endpoint"
