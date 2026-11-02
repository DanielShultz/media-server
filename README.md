# Media Server

## Назначение

`media-server` - Docker Compose-стек для личных медиасервисов.

Внутри стека:

- `qbittorrent`
- `radarr`
- `sonarr`
- `lidarr`
- `jackett`
- `flaresolverr`
- `syncthing`
- `jellyfin`

## Запуск

```bash
docker compose up -d
```

`docker compose` автоматически подхватывает `.env` из этой же папки.

Если нужен свой набор путей, скопируй `.env.example` в `.env` и заполни значения.

Остановка:

```bash
docker compose down
```

## Порты

- `8080` - qBittorrent Web UI
- `7878` - Radarr
- `8989` - Sonarr
- `8686` - Lidarr
- `9117` - Jackett
- `8384` - Syncthing Web UI
- `22000/tcp`, `22000/udp`, `21027/udp` - Syncthing sync
- `8096` - Jellyfin

## Хранилище

В compose используются внешние bind mounts и хостовые каталоги, заданные в `.env`.

Сами данные и конфиги в репозиторий не входят. В git хранится только compose-файл, документация и служебные файлы репо.

## Что в git, а что локально

Публикуются:

- `docker-compose.yml`
- `README.md`
- `.gitignore`
- `.env.example`
- `AGENTS.md`
- `VERSION`
- `compose.search-proxy.yml` и исходники/тесты `pac-connect/`.

Остаются локальными и не публикуются:

- `.env`

`.env.example` должен быть разрешен в `.gitignore` через `!.env.example`, иначе он тоже скрывается.

## Переменные

Используются такие переменные:

- `PUID`
- `PGID`
- `TZ`
- `MEDIA_POOL_DIR`
- `QBITTORRENT_CONFIG_DIR`
- `RADARR_CONFIG_DIR`
- `SONARR_CONFIG_DIR`
- `LIDARR_CONFIG_DIR`
- `JACKETT_CONFIG_DIR`
- `SYNCTHING_CONFIG_DIR`
- `JELLYFIN_CONFIG_DIR`
- `JELLYFIN_MEDIA_DIR`

Текущий образец лежит в [`.env.example`](./.env.example).

## Проверка

```bash
docker compose ps
```

Для точечной проверки Web UI обычно достаточно открыть сервисы на адресе сервера:

- `8080`
- `7878`
- `8989`
- `8686`
- `9117`
- `8384`
- `8096`

## Необязательный PAC-прокси поиска

`compose.search-proxy.yml` добавляет ограниченный CONNECT-адаптер для
Jackett/FlareSolverr. Перед запуском заполнить `PAC_URL` оригинальным HTTPS PAC
и `PAC_ALLOWED_HOSTS` точными именами из разрешённого контура в локальном `.env`.
Не публиковать PAC, proxy endpoints, cookies или рабочие конфиги.

```bash
docker compose -f docker-compose.yml -f compose.search-proxy.yml config --quiet
docker compose -f docker-compose.yml -f compose.search-proxy.yml build pac-connect
docker compose -f docker-compose.yml -f compose.search-proxy.yml up -d --no-deps pac-connect
```

Контейнер не публикует порт хоста. Сеть `search_clients` у Jackett/FlareSolverr
объявлена в основном Compose: ежедневное обновление одного Jackett сохраняет
её без override. Для подключения указать HTTP proxy `pac-connect:3128` в Jackett. Применять
только после закрытого baseline текущих proxy-полей и проверки адаптера.
Не менять qBittorrent/общие маршруты. Одного запуска контейнера для интеграции
недостаточно; сохранённые сервисы должны перечитать сеть/настройки.

Адаптер исполняет оригинальный PAC без host API: 64 MiB JS memory, 1.5 s CPU
на eval, refresh раз в 300 s, последний пригодный снимок не старше 24 h.
Разрешены CONNECT только к allowlist:443 и публичные IPv4. Для proxy-цепочек
используются исключительно HTTPS endpoints с проверкой CA/SNI; ошибка не
переходит в PROXY/DIRECT. Только исходный одиночный DIRECT разрешает прямой
выход. Неподдержанные PAC helpers дают ошибку. HTTP plaintext отклоняется;
CA MITM отсутствует. Это ограниченный адаптер, не универсальный браузерный PAC.

Проверка: `/health` адаптера, metadata Test/search, затем Torznab и Radarr без
grab/download. Нельзя считать caps восстановлением поиска. Откат: вернуть
только исходные proxy-поля Jackett, перечитать его конфиг и остановить только
адаптер; внутренняя клиентская сеть остаётся объявленной в основном Compose. Не восстанавливать
полный конфиг поверх новых правок; не делать `compose down` всего стека.

Локальные тесты после установки `pac-connect/requirements.txt`:
`python -m unittest discover -s pac-connect -v`. Docker build запускает их тоже.

## Общие риски

- Образы используют тег `latest`.
- Конфиги лежат вне репозитория, поэтому для переноса на другой сервер нужны корректные host paths.
- Стек зависит от внешних каталогов, заданных в `docker-compose.yml`.

## Граница публичной документации

Перед публикацией проверяются не только `.env`, но и `AGENTS.md`, README, примеры и содержимое отправляемых коммитов. Не добавляй реальные локальные пути, приватные адреса или ссылки на закрытые документы. GitHub-коммит не запускает Compose и не подтверждает состояние живого сервера.
