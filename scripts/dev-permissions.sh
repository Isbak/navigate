#!/usr/bin/env bash
#
# dev-permissions.sh — make the SQLite catalog and cache writable by BOTH the
# host CLI user and the Docker container, using a shared group.
#
# Why a shared group? SQLite needs write access to the database file AND its
# parent directory (it creates -wal/-shm/journal/lock files alongside the DB).
# When the host CLI user and the container user have different UIDs, the only
# way both can write the same bind-mounted files is to share a group, make the
# files group-writable, and set the setgid bit on the directories so new files
# inherit that group automatically.
#
# Usage:
#   ./scripts/dev-permissions.sh
#
# Override the defaults via environment variables:
#   NAVIGATE_UID  host user id        (default: id -u)
#   NAVIGATE_GID  shared group id      (default: id -g)
#   DATA_DIR      SQLite directory     (default: data)
#   CACHE_DIR     document cache dir    (default: cache)
set -euo pipefail

# Run from the repository root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

NAVIGATE_UID="${NAVIGATE_UID:-$(id -u)}"
NAVIGATE_GID="${NAVIGATE_GID:-$(id -g)}"
DATA_DIR="${DATA_DIR:-data}"
CACHE_DIR="${CACHE_DIR:-cache}"
DB_FILE="$DATA_DIR/catalog.sqlite"
ENV_FILE=".env"

group_name="$(getent group "$NAVIGATE_GID" 2>/dev/null | cut -d: -f1 || true)"
echo "Configuring shared-group permissions"
echo "  UID:   $NAVIGATE_UID"
echo "  GID:   $NAVIGATE_GID${group_name:+ ($group_name)}"
echo "  dirs:  $DATA_DIR/ $CACHE_DIR/"

# 1. Ensure the directories exist.
mkdir -p "$DATA_DIR" "$CACHE_DIR"

# 2. Group ownership: a numeric GID works even if the group has no name yet.
chgrp -R "$NAVIGATE_GID" "$DATA_DIR" "$CACHE_DIR"

# 3. setgid + group-write on the directories so new files inherit the group and
#    stay writable by both the host user and the container.
chmod 2775 "$DATA_DIR" "$CACHE_DIR"

# 4. Make any existing SQLite files (DB + its WAL/SHM/journal siblings)
#    group-writable. Guarded so a fresh checkout without a DB still succeeds.
shopt -s nullglob
sqlite_files=("$DB_FILE" "$DB_FILE"-wal "$DB_FILE"-shm "$DB_FILE"-journal)
for f in "${sqlite_files[@]}"; do
    [ -e "$f" ] && chmod 664 "$f"
done
shopt -u nullglob

# 5. Optional: default ACLs so EVERY future file inherits group rwX, even those
#    created by tools that ignore the setgid bit. Only when setfacl is available.
if command -v setfacl >/dev/null 2>&1; then
    setfacl -R -m "g:${NAVIGATE_GID}:rwX" -d -m "g:${NAVIGATE_GID}:rwX" \
        "$DATA_DIR" "$CACHE_DIR"
    echo "  ACLs:  applied (group $NAVIGATE_GID -> rwX, inherited)"
else
    echo "  ACLs:  skipped (setfacl not installed)"
fi

# 6. Record UID/GID in .env so 'docker compose' runs the container as this same
#    user/group (Compose reads .env automatically). Idempotent: update the two
#    keys in place if present, otherwise append; never clobber other keys.
update_env_key() {
    local key="$1" value="$2"
    if [ -f "$ENV_FILE" ] && grep -qE "^${key}=" "$ENV_FILE"; then
        # Portable in-place edit without relying on GNU sed -i semantics.
        local tmp
        tmp="$(mktemp)"
        sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp"
        mv "$tmp" "$ENV_FILE"
    else
        echo "${key}=${value}" >>"$ENV_FILE"
    fi
}
update_env_key NAVIGATE_UID "$NAVIGATE_UID"
update_env_key NAVIGATE_GID "$NAVIGATE_GID"
echo "  .env:  NAVIGATE_UID/NAVIGATE_GID written"

cat <<EOF

Done. Verify host write access with:

  touch $DATA_DIR/testfile && rm $DATA_DIR/testfile
  sqlite3 $DB_FILE "CREATE TABLE test_write(id INTEGER); DROP TABLE test_write;"

Then start the container as the same user/group:

  docker compose up --build api
EOF
