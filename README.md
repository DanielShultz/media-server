# Media Server

## Назначение

`media-server` - Docker Compose-стек для личных медиасервисов.

Внутри стека:

- `qbittorrent`
- `radarr`
- `sonarr`
- `lidarr`
- `jackett`
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

Остается только локально:

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

## Риски

- Образы используют тег `latest`.
- Конфиги лежат вне репозитория, поэтому для переноса на другой сервер нужны корректные host paths.
- Стек зависит от внешних каталогов, заданных в `docker-compose.yml`.
