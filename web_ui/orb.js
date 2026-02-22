/**
 * Carely Orb Animation.
 * Canvas-based animated orb driven by state and audio amplitude.
 */

const STATE_COLORS = {
    idle:       { r: 0,   g: 188, b: 212 },  // Cyan #00BCD4
    listening:  { r: 76,  g: 175, b: 80  },  // Green #4CAF50
    processing: { r: 255, g: 193, b: 7   },  // Yellow #FFC107
    speaking:   { r: 33,  g: 150, b: 243 },  // Blue #2196F3
    error:      { r: 244, g: 67,  b: 54  },  // Red #F44336
    emergency:  { r: 255, g: 0,   b: 0   },  // Red #FF0000
    safe_mode:  { r: 255, g: 152, b: 0   },  // Orange #FF9800
};

class OrbRenderer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');

        this.state = 'idle';
        this.amplitude = 0;
        this.targetAmplitude = 0;
        this.currentColor = { ...STATE_COLORS.idle };
        this.targetColor = { ...STATE_COLORS.idle };
        this.scale = 1.0;
        this.breathePhase = 0;
        this.spinAngle = 0;
        this.time = 0;

        this._resize();
        window.addEventListener('resize', () => this._resize());
        this._animate();
    }

    _resize() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
        this.size = Math.min(rect.width, rect.height);
        this.cx = rect.width / 2;
        this.cy = rect.height / 2;
        this.baseRadius = this.size * 0.35;
    }

    setState(newState) {
        this.state = newState;
        this.targetColor = { ...(STATE_COLORS[newState] || STATE_COLORS.idle) };
    }

    setAmplitude(value) {
        this.targetAmplitude = Math.max(0, Math.min(1, value));
    }

    _animate() {
        const dt = 1 / 60;
        this.time += dt;

        // Smooth color transition
        const colorSpeed = 0.08;
        this.currentColor.r += (this.targetColor.r - this.currentColor.r) * colorSpeed;
        this.currentColor.g += (this.targetColor.g - this.currentColor.g) * colorSpeed;
        this.currentColor.b += (this.targetColor.b - this.currentColor.b) * colorSpeed;

        // Smooth amplitude
        const ampSpeed = 0.15;
        this.amplitude += (this.targetAmplitude - this.amplitude) * ampSpeed;

        // Calculate scale based on state
        switch (this.state) {
            case 'idle':
                this.breathePhase += dt * (2 * Math.PI / 3); // 3s cycle
                this.scale = 1.0 + 0.05 * Math.sin(this.breathePhase);
                break;
            case 'listening':
                this.scale = 0.8 + 0.5 * this.amplitude;
                break;
            case 'processing':
                this.spinAngle += dt * (2 * Math.PI / 1.5); // 1.5s cycle
                this.scale = 0.95 + 0.05 * Math.sin(this.time * 4);
                break;
            case 'speaking':
                this.scale = 0.9 + 0.4 * this.amplitude;
                break;
            case 'error':
                this.breathePhase += dt * (2 * Math.PI / 2); // 2s cycle
                this.scale = 0.95 + 0.05 * Math.sin(this.breathePhase);
                break;
            case 'emergency':
                this.breathePhase += dt * (2 * Math.PI / 0.5); // 0.5s cycle
                this.scale = 0.9 + 0.15 * Math.abs(Math.sin(this.breathePhase));
                break;
            case 'safe_mode':
                this.breathePhase += dt * (2 * Math.PI / 5); // 5s cycle
                this.scale = 0.95 + 0.05 * Math.sin(this.breathePhase);
                break;
            default:
                this.scale = 1.0;
        }

        this._draw();
        requestAnimationFrame(() => this._animate());
    }

    _draw() {
        const ctx = this.ctx;
        const w = this.canvas.width / (window.devicePixelRatio || 1);
        const h = this.canvas.height / (window.devicePixelRatio || 1);

        ctx.clearRect(0, 0, w, h);

        const r = Math.round(this.currentColor.r);
        const g = Math.round(this.currentColor.g);
        const b = Math.round(this.currentColor.b);
        const radius = this.baseRadius * this.scale;

        // Outer glow
        const glowRadius = radius * (1.2 + this.amplitude * 0.5);
        const glowGrad = ctx.createRadialGradient(
            this.cx, this.cy, radius * 0.5,
            this.cx, this.cy, glowRadius
        );
        glowGrad.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.3)`);
        glowGrad.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, 0.1)`);
        glowGrad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);

        ctx.beginPath();
        ctx.arc(this.cx, this.cy, glowRadius, 0, Math.PI * 2);
        ctx.fillStyle = glowGrad;
        ctx.fill();

        // Main orb
        const orbGrad = ctx.createRadialGradient(
            this.cx - radius * 0.2, this.cy - radius * 0.2, radius * 0.1,
            this.cx, this.cy, radius
        );
        orbGrad.addColorStop(0, `rgba(${Math.min(255, r + 60)}, ${Math.min(255, g + 60)}, ${Math.min(255, b + 60)}, 0.9)`);
        orbGrad.addColorStop(0.6, `rgba(${r}, ${g}, ${b}, 0.8)`);
        orbGrad.addColorStop(1, `rgba(${Math.max(0, r - 40)}, ${Math.max(0, g - 40)}, ${Math.max(0, b - 40)}, 0.6)`);

        ctx.beginPath();
        ctx.arc(this.cx, this.cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = orbGrad;
        ctx.fill();

        // Processing spinner overlay
        if (this.state === 'processing') {
            this._drawSpinner(ctx, r, g, b, radius);
        }

        // Emergency concentric rings
        if (this.state === 'emergency') {
            this._drawEmergencyRings(ctx, r, g, b, radius);
        }
    }

    _drawSpinner(ctx, r, g, b, radius) {
        const dotCount = 8;
        const dotRadius = radius * 0.06;
        const orbitRadius = radius * 0.7;

        for (let i = 0; i < dotCount; i++) {
            const angle = this.spinAngle + (i * Math.PI * 2 / dotCount);
            const x = this.cx + Math.cos(angle) * orbitRadius;
            const y = this.cy + Math.sin(angle) * orbitRadius;
            const alpha = 0.3 + 0.7 * (i / dotCount);

            ctx.beginPath();
            ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
            ctx.fill();
        }
    }

    _drawEmergencyRings(ctx, r, g, b, radius) {
        const ringCount = 3;
        const phase = this.breathePhase;

        for (let i = 0; i < ringCount; i++) {
            const progress = ((phase / (Math.PI * 2)) + i / ringCount) % 1;
            const ringRadius = radius * (0.5 + progress * 1.0);
            const alpha = (1 - progress) * 0.4;

            ctx.beginPath();
            ctx.arc(this.cx, this.cy, ringRadius, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const orb = new OrbRenderer('orb-canvas');
    const statusEl = document.getElementById('status-text');
    const transcriptEl = document.getElementById('transcript-text');
    const mainButton = document.getElementById('main-button');
    const ws = window.carelyWS;

    const STATUS_TEXT = {
        idle: 'Ready',
        listening: 'Listening...',
        processing: 'Thinking...',
        speaking: 'Speaking...',
        error: 'Something went wrong',
        emergency: 'Emergency',
        safe_mode: 'Safe Mode',
    };

    // Wire WebSocket events to orb
    ws.on('state', (state, message) => {
        orb.setState(state);
        statusEl.textContent = message || STATUS_TEXT[state] || '';
        mainButton.className = '';
        if (state === 'listening') mainButton.className = 'listening';
        if (state === 'emergency') mainButton.className = 'emergency';
    });

    ws.on('amplitude', (value) => {
        orb.setAmplitude(value);
    });

    ws.on('transcript', (text) => {
        transcriptEl.textContent = text;
        transcriptEl.style.opacity = text ? '0.7' : '0.5';
    });

    ws.on('error', (message) => {
        statusEl.textContent = message;
    });

    ws.on('connected', () => {
        console.log('Connected to Carely backend');
    });

    ws.on('disconnected', () => {
        statusEl.textContent = 'Reconnecting...';
        orb.setState('error');
    });

    // Button press handling (with long-press detection)
    let pressStart = null;
    const LONG_PRESS_MS = 1000;

    mainButton.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        pressStart = Date.now();
    });

    mainButton.addEventListener('pointerup', (e) => {
        e.preventDefault();
        if (pressStart === null) return;
        const duration = Date.now() - pressStart;
        pressStart = null;

        if (duration >= LONG_PRESS_MS) {
            ws.sendButtonLongPress();
        } else {
            ws.sendButtonPress();
        }
    });

    mainButton.addEventListener('pointercancel', () => {
        pressStart = null;
    });

    // Keyboard support (spacebar)
    let keyPressStart = null;
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && !e.repeat) {
            e.preventDefault();
            keyPressStart = Date.now();
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.code === 'Space' && keyPressStart !== null) {
            e.preventDefault();
            const duration = Date.now() - keyPressStart;
            keyPressStart = null;

            if (duration >= LONG_PRESS_MS) {
                ws.sendButtonLongPress();
            } else {
                ws.sendButtonPress();
            }
        }
    });

    // Connect
    ws.connect();
});
