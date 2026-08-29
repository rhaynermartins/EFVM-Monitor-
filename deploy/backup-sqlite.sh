#!/bin/sh
set -eu

: "${EFVM_DATABASE_PATH:?EFVM_DATABASE_PATH não configurado}"

backup_directory="$(dirname "$EFVM_DATABASE_PATH")/backups"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_path="$backup_directory/efvm-monitor-$timestamp.db"

umask 077
mkdir -p "$backup_directory"
sqlite3 "$EFVM_DATABASE_PATH" ".timeout 5000" ".backup '$backup_path'"

if [ "$(sqlite3 "$backup_path" "PRAGMA integrity_check;")" != "ok" ]; then
    echo "O backup SQLite não passou na verificação de integridade." >&2
    exit 1
fi

find "$backup_directory" -type f -name 'efvm-monitor-*.db' -mtime +14 -delete
echo "Backup SQLite concluído: $(basename "$backup_path")"
