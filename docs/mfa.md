# MFA / OTP (Задание 1 / Задача 5)

Обязательная OTP-аутентификация (TOTP) для всех пользователей realm `reports-realm`.
Совместимо с **Google Authenticator** и **FreeOTP**.

## Что настроено

1. Required Action `CONFIGURE_TOTP` — **enabled + default** (всем новым и существующим пользователям)
2. OTP policy: TOTP, 6 цифр, период 30 сек, HmacSHA1
3. Browser flow `browser-with-mfa`:
   - после пароля — Conditional OTP
   - если OTP уже настроен — **OTP Form REQUIRED**
4. Скрипт: [`keycloak/configure-mfa.sh`](../keycloak/configure-mfa.sh)

## Как проверить

```bash
./keycloak/configure-mfa.sh   # если ещё не запускали
```

1. Открой http://localhost:3000 → Login
2. Введи логин/пароль (`prothetic1` / `prothetic123` или `john.doe` / `password`)
3. Keycloak покажет QR для настройки OTP — отсканируй в Google Authenticator / FreeOTP
4. Введи 6-значный код
5. При следующем входе — пароль **и** OTP обязательны (без кода вход невозможен)

## Admin UI

http://localhost:8080 → reports-realm → Authentication:
- Required actions → Configure OTP (Default ON)
- Flows → `browser-with-mfa` (bound as Browser flow)
