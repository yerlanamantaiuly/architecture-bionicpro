# Яндекс ID / Identity Brokering (Задание 1 / Задача 6)

## Redirect URI в кабинете Яндекса (точно так)

```
http://localhost:8080/realms/reports-realm/broker/yandex/endpoint
```

Права: **API Яндекс ID** (`login:info`, `login:email`, `login:avatar`). Диск не нужен.

## Секреты

```bash
cp secrets/yandex.env.example secrets/yandex.env
# заполнить Client ID / Secret
```

`secrets/yandex.env` в `.gitignore`.

## Настройка Keycloak

```bash
chmod +x keycloak/configure-yandex-idp.sh
./keycloak/configure-yandex-idp.sh
```

Создаёт OIDC IdP `yandex` (oauth.yandex.ru) + mappers + store tokens.

## Consent + профиль в БД

После логина через Яндекс `bionicpro-auth`:

1. Спрашивает разрешение использовать данные профиля
2. При «Разрешить» тянет профиль с `login.yandex.ru/info` (через broker token)
3. Сохраняет в SQLite `/data/profiles.db`

Эндпоинты: `POST /auth/consent/accept`, `POST /auth/consent/deny`, `GET /auth/profile`

## Проверка

1. Rebuild: `docker compose up -d --build bionicpro-auth frontend`
2. http://localhost:3000 → **Login with Yandex ID**
3. Авторизация на Яндексе → экран согласия → профиль в UI
