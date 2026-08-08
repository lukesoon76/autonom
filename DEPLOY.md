# Deploying Autonom on Render

Autonom ships as a Docker web service with a persistent disk (same shape as the
`lifeos` service). The embedding model + a prebuilt vector store are baked into
the image; `start.sh` symlinks the writable state (vector store, member accounts,
photo uploads) onto the disk so it survives redeploys.

## 1. Push to GitHub (your account)
```bash
cd ~/ChiefEpicure
git add -A && git commit -m "Render deploy: Docker + disk + bundled data"
gh repo create autonom --private --source=. --remote=origin --push
# …or create an empty repo in the GitHub UI, then:
# git remote add origin git@github.com:lukesoon76/autonom.git && git push -u origin main
```

## 2. Create the service on Render
Easiest — **New → Blueprint**, point at the repo; `render.yaml` provisions the
web service + a 1 GB disk at `/var/data`.

Or manually: **New → Web Service** → connect the repo → Runtime **Docker**,
Plan **Starter** → add a **Disk** (mount path `/var/data`, 1 GB) → set env var
`AUTONOM_DATA_DIR=/var/data` (and `HF_HOME=/opt/hf`).

## 3. Environment variables
- `ANTHROPIC_API_KEY` — optional; enables Claude answers + EatWhatGPT prose (falls
  back to ranked snippets without it). You already have this on `lifeos`.
- `AUTONOM_DATA_DIR=/var/data`, `HF_HOME=/opt/hf` — set by `render.yaml`.

First deploy takes a few minutes (Docker build bakes the model + vector store).
Health check is `/_stcore/health`.

## What persists vs. resets
- **Persists on the disk:** the vector store, member accounts, saved lists,
  reviews and uploaded photos.
- **Baked in the image (updates on redeploy):** the curated core. To refresh it,
  update `data/Master_List.xlsx` and redeploy.

## Not on Render (local-only for now)
The Instagram Graph API ingest + daily launchd jobs run on your Mac (they update
`~/eatlist`). To automate ingestion in the cloud, add a Render **Cron Job** that
runs the ingester against a mounted copy of the workbook — a follow-up.
