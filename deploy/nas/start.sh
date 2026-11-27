#!/bin/sh
set -eu

mkdir -p /app/data /app/logs
exec supervisord -n -c /etc/supervisor/supervisord.conf
