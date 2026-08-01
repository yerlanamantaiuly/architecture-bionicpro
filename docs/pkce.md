# PKCE (Задание 1 / Задача 2)

Authorization Code Grant заменён на **Authorization Code + PKCE (S256)**.

## Keycloak

Клиент `reports-frontend` в [`realm-export.json`](../keycloak/realm-export.json):

- `standardFlowEnabled: true` — Authorization Code flow
- `directAccessGrantsEnabled: false` — отключён Resource Owner Password (несовместим с безопасной схемой)
- `implicitFlowEnabled: false`
- `attributes.pkce.code.challenge.method: S256` — Keycloak требует PKCE с методом S256

## Frontend

В [`App.tsx`](../frontend/src/App.tsx) при инициализации `keycloak-js`:

```ts
initOptions: {
  onLoad: 'check-sso',
  pkceMethod: 'S256',
  checkLoginIframe: false,
}
```

Библиотека сама генерирует `code_verifier` / `code_challenge` и передаёт их в authorize + token exchange.

## Как проверить

1. `docker compose up -d --build`
2. Открыть http://localhost:3000 → Login
3. В DevTools → Network на запросе к `/realms/reports-realm/protocol/openid-connect/auth` должны быть параметры `code_challenge` и `code_challenge_method=S256`
4. На `/token` — параметр `code_verifier`
