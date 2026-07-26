# Диаграммы архитектуры BionicPRO

## Задание 1 / Задача 1 — управление учётными данными

| Файл | Описание |
|------|----------|
| [auth-c4.drawio](auth-c4.drawio) | Исходник C4 Container (draw.io) |
| [auth-c4.png](auth-c4.png) | Экспорт PNG |

### Что показано на диаграмме

1. **Унификация доступа** — учётки представительства из OpenLDAP через Keycloak User Federation; ПДн и медданные остаются в локальной PostgreSQL внутри границы страны.
2. **Безопасная работа с токенами** — `bionicpro-auth` (BFF) обменивает authorization code (PKCE) на access/refresh, хранит их у себя и отдаёт фронту только session cookie (`HttpOnly` + `Secure`).
3. **Внешние IdP по странам** — Identity Brokering (Яндекс ID и IdP других рынков).

## Задание 1 / Задача 3 — bionicpro-auth

См. [bionicpro-auth.md](bionicpro-auth.md).

## Задание 1 / Задача 4 — LDAP

См. [ldap.md](ldap.md).

## Задание 1 / Задача 5 — MFA OTP

См. [mfa.md](mfa.md).

## Задание 1 / Задача 6 — Яндекс ID

См. [yandex-id.md](yandex-id.md).
