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
# One script, two modes:
#
#   Development (default) — reuse the invoking user's own UID/GID. No new
#   accounts are created, so it needs no privileges:
#       ./scripts/dev-permissions.sh
#
#   Production/deploy — manage a dedicated service account. Set NAVIGATE_USER to
#   create (or reuse) a system group + user that owns the data, and optionally a
#   backup directory. Account creation needs root, so run under sudo:
#       sudo NAVIGATE_USER=navigate BACKUP_DIR=/var/backups/navigate \
#           DATA_DIR=/var/lib/navigate CACHE_DIR=/var/cache/navigate \
#           ./scripts/dev-permissions.sh
#
# Override the defaults via environment variables:
#   NAVIGATE_USER  service account to create/reuse  (default: none -> dev mode)
#   NAVIGATE_GROUP shared group name                (default: NAVIGATE_USER)
#   NAVIGATE_UID   host user id      (default: id of NAVIGATE_USER, else id -u)
#   NAVIGATE_GID   shared group id   (default: id of NAVIGATE_GROUP, else id -g)
#   DATA_DIR       SQLite directory                 (default: data)
#   CACHE_DIR      document cache dir               (default: cache)
#   BACKUP_DIR     backup directory                 (default: none)
set -euo pipefail

# Run from the repository root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

NAVIGATE_USER="${NAVIGATE_USER:-}"
NAVIGATE_GROUP="${NAVIGATE_GROUP:-$NAVIGATE_USER}"
DATA_DIR="${DATA_DIR:-data}"
CACHE_DIR="${CACHE_DIR:-cache}"
BACKUP_DIR="${BACKUP_DIR:-}"
DB_FILE="$DATA_DIR/catalog.sqlite"
ENV_FILE=".env"

# A privileged action (creating accounts, chowning) only works as root. Fail
# early with a clear message instead of a cryptic permission error mid-run.
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: $1 requires root; re-run under sudo" >&2
        exit 1
    fi
}

# 0. Resolve the target user/group. In production mode (NAVIGATE_USER set) we
#    create the dedicated system group + user if they do not already exist and
#    derive the UID/GID from them. In development mode we fall back to the
#    invoking user's own ids so no account management is needed.
if [ -n "$NAVIGATE_USER" ]; then
    if ! getent group "$NAVIGATE_GROUP" >/dev/null 2>&1; then
        require_root "creating group '$NAVIGATE_GROUP'"
        groupadd --system "$NAVIGATE_GROUP"
    fi
    if ! id "$NAVIGATE_USER" >/dev/null 2>&1; then
        require_root "creating user '$NAVIGATE_USER'"
        useradd --system --gid "$NAVIGATE_GROUP" --no-create-home \
            --shell /usr/sbin/nologin "$NAVIGATE_USER"
    fi
    NAVIGATE_UID="${NAVIGATE_UID:-$(id -u "$NAVIGATE_USER")}"
    NAVIGATE_GID="${NAVIGATE_GID:-$(getent group "$NAVIGATE_GROUP" | cut -d: -f3)}"
else
    NAVIGATE_UID="${NAVIGATE_UID:-$(id -u)}"
    NAVIGATE_GID="${NAVIGATE_GID:-$(id -g)}"
fi

# Directories to manage: data + cache always, backup only when requested.
dirs=("$DATA_DIR" "$CACHE_DIR")
[ -n "$BACKUP_DIR" ] && dirs+=("$BACKUP_DIR")

group_name="$(getent group "$NAVIGATE_GID" 2>/dev/null | cut -d: -f1 || true)"
echo "Configuring shared-group permissions"
echo "  user:  ${NAVIGATE_USER:-$(id -un) (current)}"
echo "  UID:   $NAVIGATE_UID"
echo "  GID:   $NAVIGATE_GID${group_name:+ ($group_name)}"
echo "  dirs:  $(printf '%s/ ' "${dirs[@]}")"

# 1. Ensure the directories exist.
mkdir -p "${dirs[@]}"

# 2. Ownership: in production give the dedicated account the files outright; in
#    development a numeric group is enough (works even if the group has no name).
if [ -n "$NAVIGATE_USER" ]; then
    require_root "changing ownership to '$NAVIGATE_USER'"
    chown -R "$NAVIGATE_UID:$NAVIGATE_GID" "${dirs[@]}"
else
    chgrp -R "$NAVIGATE_GID" "${dirs[@]}"
fi

# 3. setgid + group-write on the directories so new files inherit the group and
#    stay writable by both the host user and the container.
chmod 2775 "${dirs[@]}"

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
        "${dirs[@]}"
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
