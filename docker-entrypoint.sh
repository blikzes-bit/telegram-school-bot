#!/bin/sh
# One image, three roles. The bot and the API share database/models.py and the
# services layer, so shipping them as separate images invites a version skew
# where one half has a column the other does not know about. A single tag makes
# that impossible.
#
#   docker run --rm  <image> migrate   # apply Alembic migrations, then exit
#   docker run -d    <image> bot       # Telegram bot (long polling)
#   docker run -d    <image> web       # Mini App API + built frontend
#
# `migrate` as its own command is the point: schema upgrades stop being a side
# effect of the bot starting, so the bot and the API can come up in any order.
#
# There is no HEALTHCHECK in the image because the roles are checked
# differently — the bot touches HEARTBEAT_FILE, the API answers
# /api/v1/health. Set --health-cmd per container.
set -e

case "$1" in
  bot)
    exec python bot.py
    ;;
  web)
    # --proxy-headers matters behind a reverse proxy: without it the auth
    # rate limiter sees the proxy's IP for every request and collapses into a
    # single shared bucket, throttling real users instead of an attacker.
    # FORWARDED_ALLOW_IPS must name the proxy; it is not a wildcard by default
    # because trusting X-Forwarded-For from anyone lets a client spoof its IP.
    exec uvicorn web_api.main:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --proxy-headers \
      --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    # Anything else runs verbatim, so `docker run <image> python -c ...` and a
    # plain shell still work for debugging.
    exec "$@"
    ;;
esac
