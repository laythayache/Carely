# Carely Voice Assistant — Manual Server Setup

Step-by-step guide to install and run Carely on a fresh **Ubuntu 24.04** server without using `install.sh`.

---

## Prerequisites

- Ubuntu 24.04 (x86_64)
- Root or sudo access
- Internet connection
- PipeWire audio system running

---

## Step 1: Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential cmake git pkg-config \
    libpipewire-0.3-dev \
    pipewire pipewire-audio-client-libraries \
    python3 python3-pip python3-venv \
    libwebrtc-audio-processing-dev \
    chromium-browser \
    jq curl wget
```

---

## Step 2: Create Service User

```bash
sudo useradd --system --shell /usr/sbin/nologin --groups audio carely
sudo mkdir -p /opt/assistant/models/whisper /opt/assistant/models/piper /opt/assistant/bin
sudo mkdir -p /var/log/carely
sudo chown -R carely:carely /var/log/carely
```

---

## Step 3: Clone the Repository

```bash
sudo chown $(whoami) /opt/assistant
git clone https://github.com/laythayache/Carely.git /opt/assistant
cd /opt/assistant
```

---

## Step 4: Build whisper.cpp

```bash
git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git /tmp/whisper-cpp-build
cd /tmp/whisper-cpp-build
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
sudo cp build/bin/whisper-cli /opt/assistant/bin/whisper-cli
sudo chmod +x /opt/assistant/bin/whisper-cli
rm -rf /tmp/whisper-cpp-build
```

---

## Step 5: Build the C++ Audio Pipeline

```bash
cd /opt/assistant/audio_pipeline
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
```

This produces `/opt/assistant/audio_pipeline/build/carely-audio`.

---

## Step 6: Set Up Python Virtual Environment

```bash
cd /opt/assistant
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

---

## Step 7: Download Models

### Whisper model
```bash
wget -O /opt/assistant/models/whisper/ggml-base.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
```

Verify checksum (optional):
```bash
echo "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe  /opt/assistant/models/whisper/ggml-base.bin" | sha256sum -c
```

### Piper TTS binary
```bash
wget -O /tmp/piper.tar.gz \
    https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz
tar -xzf /tmp/piper.tar.gz -C /opt/assistant/bin --strip-components=1
sudo chmod +x /opt/assistant/bin/piper
rm /tmp/piper.tar.gz
```

### Piper voice models
```bash
# English voice
wget -O /opt/assistant/models/piper/en_US-lessac-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget -O /opt/assistant/models/piper/en_US-lessac-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Arabic voice
wget -O /opt/assistant/models/piper/ar_JO-kareem-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx
wget -O /opt/assistant/models/piper/ar_JO-kareem-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json
```

---

## Step 8: Configure Environment

```bash
cp /opt/assistant/.env.example /opt/assistant/.env
sudo chown carely:carely /opt/assistant/.env
nano /opt/assistant/.env
```

**Required setting — update at minimum:**
- `WEBHOOK_URL` — the URL of your n8n/webhook endpoint

All available settings are documented in `.env.example`.

---

## Step 9: Install systemd Services

```bash
sudo cp /opt/assistant/systemd/carely-audio.service /etc/systemd/system/
sudo cp /opt/assistant/systemd/carely.service /etc/systemd/system/
sudo cp /opt/assistant/systemd/carely-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable carely-audio carely
```

---

## Step 10: Set File Ownership

```bash
sudo chown -R carely:carely /opt/assistant
```

---

## Step 11: Start Services

```bash
sudo systemctl start carely-audio
sudo systemctl start carely
```

**Check status:**
```bash
sudo systemctl status carely-audio
sudo systemctl status carely
journalctl -u carely -f        # live logs
journalctl -u carely-audio -f  # audio pipeline logs
```

**Health check:**
```bash
curl http://localhost:8081/health
```

---

## Step 12: Open the Web UI

Open in a browser:
```
http://<server-ip>:8080
```

For kiosk mode (auto-launch Chromium fullscreen):
```bash
sudo systemctl enable carely-kiosk
sudo systemctl start carely-kiosk
```

---

## Running Manually (without systemd)

For development/debugging, run components directly:

```bash
cd /opt/assistant

# Terminal 1: Start the C++ audio pipeline
./audio_pipeline/build/carely-audio

# Terminal 2: Start the Python orchestrator
source .env
venv/bin/python -m src.main
```

---

## Updating

```bash
cd /opt/assistant
sudo -u carely bash update.sh
```

This pulls latest code, rebuilds if needed, restarts services, and auto-rolls back if the health check fails.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `carely-audio` won't start | Check PipeWire is running: `systemctl --user status pipewire` |
| Python service crashes | Check logs: `journalctl -u carely -e` |
| No audio devices | List PipeWire nodes: `pw-cli list-objects Node` and update `.env` |
| Webhook errors | Verify `WEBHOOK_URL` in `.env` is reachable from the server |
| Permission denied | Re-run `sudo chown -R carely:carely /opt/assistant` |
| Health check fails | Ensure port 8081 is not in use: `ss -tlnp | grep 8081` |
