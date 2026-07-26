#!/usr/bin/env bash
# Enable mandatory OTP (TOTP) MFA for all users in reports-realm.
set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${REALM:-reports-realm}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"
FLOW_NAME="browser-with-mfa"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=$ADMIN_USER" \
  -d "password=$ADMIN_PASS" \
  -d "grant_type=password" | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

echo "1) Enable CONFIGURE_TOTP as default required action"
curl -sf "${AUTH[@]}" -X PUT \
  "$KEYCLOAK_URL/admin/realms/$REALM/authentication/required-actions/CONFIGURE_TOTP" \
  -d '{
    "alias": "CONFIGURE_TOTP",
    "name": "Configure OTP",
    "providerId": "CONFIGURE_TOTP",
    "enabled": true,
    "defaultAction": true,
    "priority": 10,
    "config": {}
  }' >/dev/null

echo "2) Reset browserFlow to default before recreating MFA flow"
curl -sf "${AUTH[@]}" -X PUT "$KEYCLOAK_URL/admin/realms/$REALM" \
  -d "$(curl -sf "${AUTH[@]}" "$KEYCLOAK_URL/admin/realms/$REALM" | python3 -c '
import sys,json
r=json.load(sys.stdin)
r["browserFlow"]="browser"
print(json.dumps(r))
')" >/dev/null

# delete old copies
for f in "browser-with-mfa" "browser%20with%20mfa"; do
  curl -sf "${AUTH[@]}" -X DELETE \
    "$KEYCLOAK_URL/admin/realms/$REALM/authentication/flows/$f" >/dev/null 2>&1 || true
done
# also delete by alias lookup
python3 - <<PY
import json, urllib.request, urllib.parse
token="""$TOKEN"""; base="""$KEYCLOAK_URL"""; realm="""$REALM"""
req=urllib.request.Request(f"{base}/admin/realms/{realm}/authentication/flows",
  headers={"Authorization":f"Bearer {token}"})
flows=json.load(urllib.request.urlopen(req))
for f in flows:
  if f.get("alias") in ("browser-with-mfa", "browser with mfa"):
    alias=urllib.parse.quote(f["alias"], safe="")
    r=urllib.request.Request(f"{base}/admin/realms/{realm}/authentication/flows/{alias}",
      method="DELETE", headers={"Authorization":f"Bearer {token}"})
    try:
      urllib.request.urlopen(r); print("  deleted", f["alias"])
    except Exception as e:
      print("  skip", f["alias"], e)
PY

echo "3) Copy browser flow → $FLOW_NAME"
curl -sf "${AUTH[@]}" -X POST \
  "$KEYCLOAK_URL/admin/realms/$REALM/authentication/flows/browser/copy" \
  -d "{\"newName\": \"$FLOW_NAME\"}" >/dev/null

echo "4) Set OTP executions to REQUIRED"
python3 - <<PY
import json, urllib.request, urllib.parse

token = """$TOKEN"""
base = """$KEYCLOAK_URL"""
realm = """$REALM"""
flow_enc = urllib.parse.quote("""$FLOW_NAME""", safe="")

def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

executions = api("GET", f"/admin/realms/{realm}/authentication/flows/{flow_enc}/executions")
for e in executions:
    name = e.get("displayName") or ""
    pid = e.get("providerId") or ""
    need = None
    if "Conditional OTP" in name:
        need = "CONDITIONAL"  # allow first login to reach CONFIGURE_TOTP
    elif pid == "auth-otp-form":
        need = "REQUIRED"
    elif pid == "conditional-user-configured":
        need = "REQUIRED"
    if need and e.get("requirement") != need:
        e["requirement"] = need
        api("PUT", f"/admin/realms/{realm}/authentication/flows/{flow_enc}/executions", e)
        print(f"  set {name or pid} → {need}")
    elif need:
        print(f"  ok {name or pid} already {need}")
PY

echo "5) Bind MFA flow + OTP policy"
curl -sf "${AUTH[@]}" -X PUT "$KEYCLOAK_URL/admin/realms/$REALM" \
  -d "$(curl -sf "${AUTH[@]}" "$KEYCLOAK_URL/admin/realms/$REALM" | python3 -c '
import sys, json
r = json.load(sys.stdin)
r["otpPolicyType"] = "totp"
r["otpPolicyAlgorithm"] = "HmacSHA1"
r["otpPolicyDigits"] = 6
r["otpPolicyPeriod"] = 30
r["otpPolicyLookAheadWindow"] = 1
r["otpPolicyInitialCounter"] = 0
r["otpSupportedApplications"] = ["FreeOTP", "Google Authenticator"]
r["browserFlow"] = "browser-with-mfa"
print(json.dumps(r))
')" >/dev/null

echo "6) Add CONFIGURE_TOTP to all existing users"
python3 - <<PY
import json, urllib.request
token="""$TOKEN"""; base="""$KEYCLOAK_URL"""; realm="""$REALM"""

def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

for u in api("GET", f"/admin/realms/{realm}/users?max=200"):
    actions = set(u.get("requiredActions") or [])
    actions.add("CONFIGURE_TOTP")
    u["requiredActions"] = list(actions)
    api("PUT", f"/admin/realms/{realm}/users/{u['id']}", u)
    print(" ", u.get("username"), "→ CONFIGURE_TOTP")
PY

echo
echo "MFA configured."
echo "Login: password → QR setup (Google Authenticator / FreeOTP) → OTP code."
