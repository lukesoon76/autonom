#!/usr/bin/env bash
# Render entrypoint: point the writable state at the persistent disk (so member
# accounts, uploads and new reviews survive redeploys), seed the disk from the
# image's baked copies on first boot, then launch Streamlit. No-op friendly for
# local runs (only activates when the disk dir exists / AUTONOM_DATA_DIR is set).
set -e
DISK="${AUTONOM_DATA_DIR:-/var/data}"

if [ -d "$DISK" ] || mkdir -p "$DISK" 2>/dev/null; then
  # config (accounts, prefs, feeds) — seed defaults once, then symlink
  [ -d "$DISK/config" ] || cp -r /app/config "$DISK/config"
  rm -rf /app/config && ln -s "$DISK/config" /app/config

  # community photo uploads — persist on disk, served via ./static/uploads
  mkdir -p "$DISK/uploads" /app/static
  rm -rf /app/static/uploads && ln -s "$DISK/uploads" /app/static/uploads

  # vector store — seed from the baked copy on first boot, then symlink
  if [ -z "$(ls -A "$DISK/chroma_db" 2>/dev/null)" ]; then
    mkdir -p "$DISK/chroma_db"
    [ -d /app/_seed_chroma ] && cp -a /app/_seed_chroma/. "$DISK/chroma_db/"
    # stamp the seeded workbook version so cloud_sync only re-runs on real changes
    [ -f /app/data/Master_List.xlsx ] && \
      sha256sum /app/data/Master_List.xlsx | awk '{print $1}' > "$DISK/core_version.txt"
  fi
  rm -rf /app/chroma_db && ln -s "$DISK/chroma_db" /app/chroma_db
fi

exec streamlit run app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false
