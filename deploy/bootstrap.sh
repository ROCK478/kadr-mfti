#!/usr/bin/env bash
set -euo pipefail
# Run on a fresh Ubuntu 24.04 VPS after extracting the source bundle.
if [[ ${EUID} -ne 0 ]]; then echo 'Запустите через sudo.' >&2; exit 1; fi
kadr_domain=${1:-}
if [[ ! $kadr_domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z0-9-]+$ ]]; then
  echo 'Укажите доменное имя, направленное на этот VPS: sudo bash deploy/bootstrap.sh kadr.example.com' >&2; exit 1
fi
kadr_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$kadr_root"
if (( $(awk '/MemTotal/{print $2}' /proc/meminfo) < 7000000 )); then
  echo 'Для этой модели и речи нужен сервер с 8 ГБ RAM.' >&2; exit 1
fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 ca-certificates
fi
systemctl enable --now docker
mkdir -p models
kadr_threads=$(nproc)
if (( kadr_threads > 4 )); then kadr_threads=4; fi
printf 'KADR_DOMAIN=%s\nMODEL_THREADS=%s\nMODEL_TIMEOUT=240\n' "$kadr_domain" "$kadr_threads" > deploy/.env
chmod 600 deploy/.env
if command -v ufw >/dev/null && ufw status | head -1 | grep -q 'Status: active'; then
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
fi
docker compose --env-file deploy/.env -f deploy/compose.yaml build app
docker run --rm --user 0:0 --volume "$kadr_root/models:/models" kadr:server python scripts/download_model.py --directory /models
docker run --rm --user 0:0 --volume "$kadr_root/models:/models" kadr:server python scripts/download_speech_models.py --directory /models/speech
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
echo "Кадр запущен: https://$kadr_domain"
echo 'Сертификату может потребоваться несколько минут. Проверка: curl -f https://ИМЯ/health'
