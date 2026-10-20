#!/usr/bin/env sh
# Демонстрационный режим: Система доступна другим устройствам этой сети Wi-Fi.
exec "$(dirname "$0")/run_unix.sh" --share "$@"
