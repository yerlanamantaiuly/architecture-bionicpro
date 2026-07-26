# bionicpro-auth (Задание 1 / Задача 3)

BFF-сервис: обменивает authorization code на токены у Keycloak, хранит их на сервере,
отдаёт фронту только session cookie (`HttpOnly`; `Secure` включается через `COOKIE_SECURE=true`).

## Эндпоинты

| Method | Path | Описание |
|--------|------|----------|
| GET | `/auth/login` | Старт PKCE → редирект в Keycloak |
| GET | `/auth/callback` | Code exchange, создание сессии, Set-Cookie |
| GET | `/auth/me` | Текущий пользователь + ротация session id |
| GET | `/auth/session` | Проверка сессии + silent refresh + ротация |
| GET | `/auth/token` | Access token для доверенных бэкендов (cookie-gated) |
| POST | `/auth/logout` | Выход |

## Свойства безопасности

- `access_token` TTL в Keycloak = **120 секунд** (`accessTokenLifespan`)
- `SESSION_TTL_SECONDS` = 1800 (> access TTL) — silent refresh через `refresh_token`
- `refresh_token` хранится **зашифрованным** (Fernet) в памяти процесса
- `access_token` — в памяти, привязан к `session_id`
- Cookie: `HttpOnly`, `Secure` (prod), `SameSite=Lax`
- На каждом защищённом запросе — **ротация session id** (anti session fixation)

## Frontend

Убраны `keycloak-js` / `@react-keycloak/web`. Логин = redirect на `/auth/login`.
API-вызовы с `credentials: 'include'`.

## Пересоздание Keycloak realm

Realm импортируется один раз. После смены `realm-export.json`:

```bash
docker compose down
sudo rm -rf postgres-keycloak-data
docker compose up -d --build
```

## Проверка

1. http://localhost:3000 → Login
2. DevTools → Application → Cookies → `bionicpro_session` (HttpOnly)
3. В Network **нет** access/refresh токенов в ответах фронту
4. Повторный `/auth/me` — новый `session_id` в JSON и в cookie
