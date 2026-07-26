# LDAP / User Federation (Задание 1 / Задача 4)

OpenLDAP — источник учётных записей представительства BionicPRO в другой стране.
Keycloak подключается через User Federation и маппит LDAP-группы на realm-роли.

## Состав

| Компонент | Описание |
|-----------|----------|
| `ldap` в docker-compose | OpenLDAP (`osixia/openldap`), домен `dc=example,dc=com` |
| [`ldap/config.ldif`](../ldap/config.ldif) | Пользователи и группы (роли) |
| [`ldap/configure-keycloak-ldap.sh`](../ldap/configure-keycloak-ldap.sh) | Настройка federation + role mapper через Admin API |

## Пользователи LDAP

| uid | password | LDAP-группа → realm role |
|-----|----------|--------------------------|
| `john.doe` | `password` | `prothetic_user` |
| `alex.johnson` | `password` | `prothetic_user` |
| `jane.smith` | `password` | `user` |

## Поднять и настроить

```bash
docker compose up -d ldap
docker compose up ldap-init   # загружает config.ldif
# дождаться Keycloak:
chmod +x ldap/configure-keycloak-ldap.sh
./ldap/configure-keycloak-ldap.sh
```

Скрипт создаёт провайдер `ldap-bionicpro` (`ldap://ldap:389`) и mapper `realm-roles`
(группы из `ou=Groups` → realm roles с тем же именем).

## Проверка

1. Keycloak Admin → Realm **reports-realm** → User federation → **ldap-bionicpro**
2. Mappers → **realm-roles** (role-ldap-mapper)
3. Users → синхронизированные `john.doe` / `jane.smith` / `alex.johnson`
4. Логин через UI: http://localhost:3000 → Login → `john.doe` / `password`
