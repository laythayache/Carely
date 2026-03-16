/**
 * Carely WebSocket client.
 * Connects to the Python backend, receives state/amplitude updates,
 * sends button presses.
 */

const WS_RECONNECT_DELAY_MS = 2000;
const WS_MAX_RECONNECT_DELAY_MS = 30000;

class CarelyWSClient {
    constructor() {
        this.ws = null;
        this.reconnectDelay = WS_RECONNECT_DELAY_MS;
        this.reconnectTimer = null;
        this.listeners = {
            state: [],
            amplitude: [],
            transcript: [],
            response: [],
            error: [],
            log: [],
            connected: [],
            disconnected: [],
        };
        this.currentState = 'idle';
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws`;

        try {
            this.ws = new WebSocket(url);
        } catch (e) {
            console.error('WebSocket connection failed:', e);
            this._scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectDelay = WS_RECONNECT_DELAY_MS;
            this._emit('connected');
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this._emit('disconnected');
            this._scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket error:', err);
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                this._handleMessage(msg);
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };
    }

    _handleMessage(msg) {
        switch (msg.type) {
            case 'state':
                this.currentState = msg.state;
                this._emit('state', msg.state, msg.message || '');
                break;
            case 'amplitude':
                this._emit('amplitude', msg.value);
                break;
            case 'transcript':
                this._emit('transcript', msg.text, msg.language || '');
                break;
            case 'response':
                this._emit('response', msg.text, msg.language || '');
                break;
            case 'error':
                this._emit('error', msg.message, msg.code || '');
                break;
            case 'log':
                this._emit('log', msg.message, msg.level || 'info');
                break;
            default:
                console.warn('Unknown message type:', msg.type);
        }
    }

    sendButtonPress() {
        this._send({ type: 'button', action: 'press' });
    }

    sendButtonLongPress() {
        this._send({ type: 'button', action: 'long_press' });
    }

    _send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    _scheduleReconnect() {
        if (this.reconnectTimer) return;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
            // Exponential backoff with cap
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 1.5,
                WS_MAX_RECONNECT_DELAY_MS
            );
        }, this.reconnectDelay);
    }

    on(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event].push(callback);
        }
    }

    _emit(event, ...args) {
        const handlers = this.listeners[event] || [];
        for (const handler of handlers) {
            try {
                handler(...args);
            } catch (e) {
                console.error(`Listener error for ${event}:`, e);
            }
        }
    }
}

// Global instance
window.carelyWS = new CarelyWSClient();
