#!/bin/sh
# Create .env from .env.example with random database passwords. Refuses to overwrite an existing .env.
# Passwords are applied when the database volumes are first created; for existing volumes, change them
# with ALTER ROLE as well.
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
  echo ".env already exists; not overwriting." >&2
  exit 1
fi
rand() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }
sed -e "s|^APP_DB_PASSWORD=.*|APP_DB_PASSWORD=$(rand)|" \
    -e "s|^SOURCE_DB_ADMIN_PASSWORD=.*|SOURCE_DB_ADMIN_PASSWORD=$(rand)|" \
    -e "s|^SOURCE_READER_PASSWORD=.*|SOURCE_READER_PASSWORD=$(rand)|" \
    .env.example > .env
chmod 600 .env
echo "Wrote .env with generated passwords."
