#!/usr/bin/env bash
# ============================================================
# Carely Voice Assistant — Update + Rollback Script
# Pulls latest code, rebuilds if needed, restarts services.
# Rolls back automatically if health check fails.
# ============================================================
set -euo pipefail

INSTALL_DIR="/opt/assistant"
HEALTH_URL="http://localhost:8081/health"
ROLLBACK_TIMEOUT=60

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { log "ERROR: $*"; exit 1; }

cd "$INSTALL_DIR"

# ── Save Current State ──────────────────────────────────────
PREV_COMMIT=$(git rev-parse HEAD)
log "Current commit: ${PREV_COMMIT:0:12}"

# ── Pull Latest ─────────────────────────────────────────────
log "Pulling latest from main..."
git pull origin main || { log "Git pull failed, staying on ${PREV_COMMIT:0:12}"; exit 1; }
NEW_COMMIT=$(git rev-parse HEAD)

if [ "$PREV_COMMIT" = "$NEW_COMMIT" ]; then
    log "Already up to date. No restart needed."
    exit 0
fi

log "Updated to: ${NEW_COMMIT:0:12}"

# ── Rebuild C++ Pipeline If Changed ─────────────────────────
if git diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" | grep -q "^audio_pipeline/"; then
    log "Audio pipeline sources changed, rebuilding..."
    cd audio_pipeline/build
    cmake .. -DCMAKE_BUILD_TYPE=Release
    make -j"$(nproc)"
    cd "$INSTALL_DIR"
    log "Audio pipeline rebuilt"
fi

# ── Update Python Dependencies If Changed ───────────────────
if git diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" | grep -q "requirements.txt"; then
    log "Python dependencies changed, updating..."
    "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt -q
    log "Python dependencies updated"
fi

# ── Restart Services ────────────────────────────────────────
log "Restarting services..."
sudo systemctl restart carely-audio carely

# ── Health Check ────────────────────────────────────────────
log "Waiting for health check (${ROLLBACK_TIMEOUT}s timeout)..."
ELAPSED=0
while [ $ELAPSED -lt $ROLLBACK_TIMEOUT ]; do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        HEALTH_RESPONSE=$(curl -sf "$HEALTH_URL")
        log "Health check passed: $HEALTH_RESPONSE"
        log "Update successful: ${PREV_COMMIT:0:12} -> ${NEW_COMMIT:0:12}"
        exit 0
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if [ $((ELAPSED % 10)) -eq 0 ]; then
        log "Still waiting... (${ELAPSED}s / ${ROLLBACK_TIMEOUT}s)"
    fi
done

# ── Rollback ────────────────────────────────────────────────
log "Health check FAILED after ${ROLLBACK_TIMEOUT}s"
log "Rolling back to ${PREV_COMMIT:0:12}..."

git checkout "$PREV_COMMIT"

# Rebuild C++ if it was changed
if git diff --name-only "$NEW_COMMIT" "$PREV_COMMIT" | grep -q "^audio_pipeline/"; then
    log "Rebuilding audio pipeline for rollback..."
    cd audio_pipeline/build
    cmake .. -DCMAKE_BUILD_TYPE=Release
    make -j"$(nproc)"
    cd "$INSTALL_DIR"
fi

# Restore Python deps if changed
if git diff --name-only "$NEW_COMMIT" "$PREV_COMMIT" | grep -q "requirements.txt"; then
    "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt -q
fi

sudo systemctl restart carely-audio carely
log "Rolled back to ${PREV_COMMIT:0:12}. Services restarted."
exit 1
