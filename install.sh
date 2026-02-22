#!/usr/bin/env bash
# ============================================================
# Carely Voice Assistant — Installer Script
# Installs all dependencies, builds components, downloads models.
# Run on a fresh Ubuntu 24.04 system.
# ============================================================
set -euo pipefail

INSTALL_DIR="/opt/assistant"
MODEL_DIR="${INSTALL_DIR}/models"
VENV_DIR="${INSTALL_DIR}/venv"
BIN_DIR="${INSTALL_DIR}/bin"
SERVICE_USER="carely"

# Model URLs and checksums
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
WHISPER_MODEL_SHA256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"

PIPER_RELEASE="2023.11.14-2"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_RELEASE}/piper_linux_x86_64.tar.gz"

PIPER_VOICE_EN_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
PIPER_VOICE_EN_JSON="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
PIPER_VOICE_AR_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx"
PIPER_VOICE_AR_JSON="https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json"

# ── Helpers ──────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { log "ERROR: $*"; exit 1; }
step() { echo ""; log "=== $* ==="; }

check_sha256() {
    local file="$1" expected="$2"
    local actual
    actual=$(sha256sum "$file" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        die "Checksum mismatch for $file\n  expected: $expected\n  got:      $actual"
    fi
    log "Checksum OK: $(basename "$file")"
}

# ── Step 1: System Dependencies ─────────────────────────────
install_system_deps() {
    step "Installing system dependencies"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        build-essential cmake git pkg-config \
        libpipewire-0.3-dev \
        pipewire pipewire-audio-client-libraries \
        python3 python3-pip python3-venv \
        libwebrtc-audio-processing-dev \
        chromium-browser \
        jq curl wget
    log "System dependencies installed"
}

# ── Step 2: Create Service User ─────────────────────────────
setup_user() {
    step "Setting up service user"
    if ! id "$SERVICE_USER" &>/dev/null; then
        sudo useradd --system --shell /usr/sbin/nologin --groups audio "$SERVICE_USER"
        log "Created user: $SERVICE_USER"
    else
        log "User $SERVICE_USER already exists"
    fi
    sudo mkdir -p "$INSTALL_DIR" "$MODEL_DIR/whisper" "$MODEL_DIR/piper" "$BIN_DIR"
    sudo mkdir -p /var/log/carely
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" /var/log/carely
}

# ── Step 3: Clone or Update Repository ──────────────────────
setup_repo() {
    step "Setting up repository"
    if [ -d "$INSTALL_DIR/.git" ]; then
        log "Repository exists, pulling latest..."
        cd "$INSTALL_DIR" && git pull origin main
    else
        log "Cloning repository..."
        sudo chown "$(whoami)" "$INSTALL_DIR"
        git clone https://github.com/laythayache/Carely.git "$INSTALL_DIR"
    fi
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

# ── Step 4: Build whisper.cpp ────────────────────────────────
build_whisper_cpp() {
    step "Building whisper.cpp"
    local build_dir="/tmp/whisper-cpp-build"
    rm -rf "$build_dir"
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git "$build_dir"
    cd "$build_dir"
    cmake -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build --config Release -j"$(nproc)"
    sudo cp build/bin/whisper-cli "$BIN_DIR/whisper-cli"
    sudo chmod +x "$BIN_DIR/whisper-cli"
    rm -rf "$build_dir"
    log "whisper.cpp built: $BIN_DIR/whisper-cli"
}

# ── Step 5: Build C++ Audio Pipeline ────────────────────────
build_audio_pipeline() {
    step "Building C++ audio pipeline"
    cd "$INSTALL_DIR/audio_pipeline"
    mkdir -p build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release
    make -j"$(nproc)"
    log "Audio pipeline built: $INSTALL_DIR/audio_pipeline/build/carely-audio"
}

# ── Step 6: Python Virtual Environment ──────────────────────
setup_python() {
    step "Setting up Python virtual environment"
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
    log "Python dependencies installed"
}

# ── Step 7: Download Models ─────────────────────────────────
download_models() {
    step "Downloading models"

    if [ ! -f "$MODEL_DIR/whisper/ggml-base.bin" ]; then
        log "Downloading Whisper base model..."
        wget -q --show-progress -O "$MODEL_DIR/whisper/ggml-base.bin" "$WHISPER_MODEL_URL"
        check_sha256 "$MODEL_DIR/whisper/ggml-base.bin" "$WHISPER_MODEL_SHA256"
    else
        log "Whisper model already exists"
    fi

    if [ ! -f "$BIN_DIR/piper" ]; then
        log "Downloading Piper binary..."
        wget -q --show-progress -O /tmp/piper.tar.gz "$PIPER_URL"
        tar -xzf /tmp/piper.tar.gz -C "$BIN_DIR" --strip-components=1
        rm /tmp/piper.tar.gz
        sudo chmod +x "$BIN_DIR/piper"
    else
        log "Piper binary already exists"
    fi

    if [ ! -f "$MODEL_DIR/piper/en_US-lessac-medium.onnx" ]; then
        log "Downloading English voice..."
        wget -q --show-progress -O "$MODEL_DIR/piper/en_US-lessac-medium.onnx" "$PIPER_VOICE_EN_URL"
        wget -q --show-progress -O "$MODEL_DIR/piper/en_US-lessac-medium.onnx.json" "$PIPER_VOICE_EN_JSON"
    fi

    if [ ! -f "$MODEL_DIR/piper/ar_JO-kareem-medium.onnx" ]; then
        log "Downloading Arabic voice..."
        wget -q --show-progress -O "$MODEL_DIR/piper/ar_JO-kareem-medium.onnx" "$PIPER_VOICE_AR_URL"
        wget -q --show-progress -O "$MODEL_DIR/piper/ar_JO-kareem-medium.onnx.json" "$PIPER_VOICE_AR_JSON"
    fi

    log "All models downloaded"
}

# ── Step 8: Install systemd Services ────────────────────────
install_services() {
    step "Installing systemd services"
    sudo cp "$INSTALL_DIR/systemd/carely-audio.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/systemd/carely.service" /etc/systemd/system/
    sudo cp "$INSTALL_DIR/systemd/carely-kiosk.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable carely-audio carely
    log "Services installed and enabled"
}

# ── Step 9: Setup Configuration ─────────────────────────────
setup_config() {
    step "Setting up configuration"
    if [ ! -f "$INSTALL_DIR/.env" ]; then
        sudo cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        sudo chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
        log "Created .env from template"
        log ">>> IMPORTANT: Edit /opt/assistant/.env before starting! <<<"
    else
        log ".env already exists, skipping"
    fi
}

# ── Step 10: Set CPU Governor ────────────────────────────────
set_cpu_governor() {
    step "Setting CPU governor to performance"
    if command -v cpufreq-set &>/dev/null; then
        for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            echo performance | sudo tee "$cpu" > /dev/null 2>&1 || true
        done
        log "CPU governor set to performance"
    else
        log "cpufreq not available, skipping"
    fi
}

# ── Step 11: Validate Installation ──────────────────────────
validate_install() {
    step "Validating installation"
    local errors=0

    [ -f "$BIN_DIR/whisper-cli" ]                        || { log "MISSING: whisper-cli"; errors=$((errors+1)); }
    [ -f "$BIN_DIR/piper" ]                              || { log "MISSING: piper"; errors=$((errors+1)); }
    [ -f "$MODEL_DIR/whisper/ggml-base.bin" ]             || { log "MISSING: Whisper model"; errors=$((errors+1)); }
    [ -f "$MODEL_DIR/piper/en_US-lessac-medium.onnx" ]   || { log "MISSING: English voice"; errors=$((errors+1)); }
    [ -f "$MODEL_DIR/piper/ar_JO-kareem-medium.onnx" ]   || { log "MISSING: Arabic voice"; errors=$((errors+1)); }
    "$VENV_DIR/bin/python" -c "import aiohttp; import jsonschema; import pynput" || { log "MISSING: Python deps"; errors=$((errors+1)); }

    if [ "$errors" -gt 0 ]; then
        die "$errors validation errors found"
    fi
    log "All validation checks passed"
}

# ── Main ─────────────────────────────────────────────────────
main() {
    log "============================================"
    log "   Carely Voice Assistant — Installer"
    log "============================================"

    install_system_deps
    setup_user
    setup_repo
    build_whisper_cpp
    build_audio_pipeline
    setup_python
    download_models
    install_services
    setup_config
    set_cpu_governor
    validate_install

    log ""
    log "============================================"
    log "   Installation Complete!"
    log "============================================"
    log ""
    log "Next steps:"
    log "  1. Edit /opt/assistant/.env (set WEBHOOK_URL)"
    log "  2. sudo systemctl start carely-audio carely"
    log "  3. Open http://$(hostname -I | awk '{print $1}'):8080"
    log ""
}

main "$@"
