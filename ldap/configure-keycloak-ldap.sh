#!/usr/bin/env bash
# Configure Keycloak User Federation (OpenLDAP) + role mapper.
# Usage: ./ldap/configure-keycloak-ldap.sh
set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${REALM:-reports-realm}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"
LDAP_URL="${LDAP_URL:-ldap://ldap:389}"
LDAP_BIND_DN="${LDAP_BIND_DN:-cn=admin,dc=example,dc=com}"
LDAP_BIND_CRED="${LDAP_BIND_CRED:-admin}"

echo "Waiting for Keycloak..."
for i in $(seq 1 60); do
  if curl -sf "$KEYCLOAK_URL" >/dev/null; then
    break
  fi
  sleep 2
done

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=$ADMIN_USER" \
  -d "password=$ADMIN_PASS" \
  -d "grant_type=password" | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# Remove existing LDAP providers with same name
EXISTING=$(curl -sf "${AUTH[@]}" \
  "$KEYCLOAK_URL/admin/realms/$REALM/components?type=org.keycloak.storage.UserStorageProvider" || echo '[]')

echo "$EXISTING" | python3 -c "
import sys, json, urllib.request, os
comps = json.load(sys.stdin)
token = '''$TOKEN'''
base = '''$KEYCLOAK_URL'''
realm = '''$REALM'''
for c in comps:
    if c.get('name') == 'ldap-bionicpro' or c.get('providerId') == 'ldap':
        req = urllib.request.Request(
            f'{base}/admin/realms/{realm}/components/{c[\"id\"]}',
            method='DELETE',
            headers={'Authorization': f'Bearer {token}'},
        )
        try:
            urllib.request.urlopen(req)
            print('deleted', c.get('name'), c['id'])
        except Exception as e:
            print('skip delete', e)
"

# Create LDAP User Storage Provider
PROVIDER_PAYLOAD=$(cat <<EOF
{
  "name": "ldap-bionicpro",
  "providerId": "ldap",
  "providerType": "org.keycloak.storage.UserStorageProvider",
  "config": {
    "enabled": ["true"],
    "priority": ["0"],
    "fullSyncPeriod": ["-1"],
    "changedSyncPeriod": ["-1"],
    "cachePolicy": ["DEFAULT"],
    "evictionDay": [],
    "evictionHour": [],
    "evictionMinute": [],
    "maxLifespan": [],
    "batchSizeForSync": ["1000"],
    "editMode": ["READ_ONLY"],
    "syncRegistrations": ["false"],
    "vendor": ["other"],
    "usernameLDAPAttribute": ["uid"],
    "rdnLDAPAttribute": ["uid"],
    "uuidLDAPAttribute": ["entryUUID"],
    "userObjectClasses": ["inetOrgPerson, organizationalPerson"],
    "connectionUrl": ["$LDAP_URL"],
    "usersDn": ["ou=People,dc=example,dc=com"],
    "authType": ["simple"],
    "bindDn": ["$LDAP_BIND_DN"],
    "bindCredential": ["$LDAP_BIND_CRED"],
    "searchScope": ["1"],
    "useTruststoreSpi": ["ldapsOnly"],
    "connectionPooling": ["true"],
    "pagination": ["true"],
    "importEnabled": ["true"],
    "trustEmail": ["true"],
    "usePasswordModifyExtendedOp": ["false"],
    "validatePasswordPolicy": ["false"],
    "allowKerberosAuthentication": ["false"],
    "connectionTimeout": [],
    "readTimeout": [],
    "customUserSearchFilter": []
  }
}
EOF
)

curl -sf "${AUTH[@]}" -X POST \
  "$KEYCLOAK_URL/admin/realms/$REALM/components" \
  -d "$PROVIDER_PAYLOAD" >/dev/null
echo "Created LDAP provider ldap-bionicpro"

PROVIDER_ID=$(curl -sf "${AUTH[@]}" \
  "$KEYCLOAK_URL/admin/realms/$REALM/components?type=org.keycloak.storage.UserStorageProvider" \
  | python3 -c 'import sys,json; print([c["id"] for c in json.load(sys.stdin) if c.get("name")=="ldap-bionicpro"][0])')
echo "Provider id: $PROVIDER_ID"

# Default mappers are auto-created. Add / replace role-ldap-mapper.
MAPPERS=$(curl -sf "${AUTH[@]}" \
  "$KEYCLOAK_URL/admin/realms/$REALM/components?parent=$PROVIDER_ID&type=org.keycloak.storage.ldap.mappers.LDAPStorageMapper")

# Delete existing role mappers named realm-role-mapper
echo "$MAPPERS" | python3 -c "
import sys, json, urllib.request
comps = json.load(sys.stdin)
token = '''$TOKEN'''
base = '''$KEYCLOAK_URL'''
realm = '''$REALM'''
for c in comps:
    if c.get('providerId') == 'role-ldap-mapper' or c.get('name') == 'realm-roles':
        req = urllib.request.Request(
            f'{base}/admin/realms/{realm}/components/{c[\"id\"]}',
            method='DELETE',
            headers={'Authorization': f'Bearer {token}'},
        )
        try:
            urllib.request.urlopen(req)
            print('deleted mapper', c.get('name'))
        except Exception as e:
            print('skip', e)
"

ROLE_MAPPER=$(cat <<EOF
{
  "name": "realm-roles",
  "providerId": "role-ldap-mapper",
  "providerType": "org.keycloak.storage.ldap.mappers.LDAPStorageMapper",
  "parentId": "$PROVIDER_ID",
  "config": {
    "roles.dn": ["ou=Groups,dc=example,dc=com"],
    "role.name.ldap.attribute": ["cn"],
    "role.object.classes": ["groupOfNames"],
    "membership.ldap.attribute": ["member"],
    "membership.attribute.type": ["DN"],
    "membership.user.ldap.attribute": ["uid"],
    "roles.ldap.filter": [],
    "mode": ["READ_ONLY"],
    "user.roles.retrieve.strategy": ["LOAD_ROLES_BY_MEMBER_ATTRIBUTE"],
    "memberof.ldap.attribute": ["memberOf"],
    "use.realm.roles.mapping": ["true"],
    "client.id": []
  }
}
EOF
)

curl -sf "${AUTH[@]}" -X POST \
  "$KEYCLOAK_URL/admin/realms/$REALM/components" \
  -d "$ROLE_MAPPER" >/dev/null
echo "Created role-ldap-mapper (LDAP groups → realm roles)"

# Trigger full sync of users
curl -sf "${AUTH[@]}" -X POST \
  "$KEYCLOAK_URL/admin/realms/$REALM/user-storage/$PROVIDER_ID/sync?action=triggerFullSync" \
  | python3 -m json.tool || true

echo "LDAP federation configured."
echo "Test users: john.doe / password, jane.smith / password, alex.johnson / password"
