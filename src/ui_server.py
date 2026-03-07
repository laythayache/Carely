"""
Carely WebSocket + static file server.
Serves the web UI and provides real-time state/amplitude updates via WebSocket.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)

WEB_UI_DIR = Path(__file__).parent.parent / "web_ui"


class UIServer:
    """
    HTTP server that:
    - Serves static files from web_ui/
    - Provides WebSocket endpoint at /ws for real-time updates
    - Accepts button press events from the UI
    """

    def __init__(self, host: str, port: int, event_queue: asyncio.Queue):
        self.host = host
        self.port = port
        self.event_queue = event_queue
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Start the HTTP/WebSocket server."""
        self._app = web.Application()
        self._app.router.add_get("/ws", self._ws_handler)
        # Static files: serve index.html at root, other files by name
        self._app.router.add_get("/", self._index_handler)
        self._app.router.add_static("/", WEB_UI_DIR, show_index=False)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"UI server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Gracefully shutdown the server."""
        # Close all WebSocket connections
        for ws in list(self._ws_clients):
            await ws.close()
        self._ws_clients.clear()

        if self._runner:
            await self._runner.cleanup()
        logger.info("UI server stopped")

    async def _index_handler(self, request: web.Request) -> web.FileResponse:
        """Serve index.html."""
        return web.FileResponse(WEB_UI_DIR / "index.html")

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        client_ip = request.remote
        logger.info(f"WebSocket client connected: {client_ip}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from WebSocket: {msg.data}")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._ws_clients.discard(ws)
            logger.info(f"WebSocket client disconnected: {client_ip}")

        return ws

    async def _handle_ws_message(self, data: dict[str, Any]) -> None:
        """Process incoming WebSocket messages (button presses from UI)."""
        msg_type = data.get("type")
        logger.info(f"[UI] Received WS message: {data}")
        if msg_type == "button":
            action = data.get("action", "press")
            logger.info(f"[UI] Button event from web UI: action={action}")
            await self.event_queue.put(("ui_button", action))
        else:
            logger.warning(f"[UI] Unknown WebSocket message type: {msg_type}")

    async def broadcast_state(self, state: str, message: str = "") -> None:
        """Send state update to all connected WebSocket clients."""
        logger.info(f"[UI] Broadcasting state='{state}' to {len(self._ws_clients)} clients")
        await self._broadcast({"type": "state", "state": state, "message": message})

    async def broadcast_amplitude(self, value: float) -> None:
        """Send amplitude value to all connected WebSocket clients."""
        await self._broadcast({"type": "amplitude", "value": round(value, 3)})

    async def broadcast_transcript(self, text: str, language: str = "") -> None:
        """Send transcript text to all connected WebSocket clients."""
        logger.info(f"[UI] Broadcasting transcript: '{text[:80]}' lang={language} to {len(self._ws_clients)} clients")
        await self._broadcast({"type": "transcript", "text": text, "language": language})

    async def broadcast_response(self, text: str, language: str = "") -> None:
        """Send AI response text to all connected WebSocket clients."""
        logger.info(f"[UI] Broadcasting response: '{text[:80]}' lang={language} to {len(self._ws_clients)} clients")
        await self._broadcast({"type": "response", "text": text, "language": language})

    async def broadcast_error(self, message: str, code: str = "") -> None:
        """Send error message to all connected WebSocket clients."""
        logger.error(f"[UI] Broadcasting error: '{message}' code={code} to {len(self._ws_clients)} clients")
        await self._broadcast({"type": "error", "message": message, "code": code})

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Send a message to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        payload = json.dumps(data)
        disconnected = set()

        for ws in self._ws_clients:
            try:
                await ws.send_str(payload)
            except (ConnectionResetError, ConnectionError):
                disconnected.add(ws)
            except Exception:
                logger.exception("Error broadcasting to WebSocket client")
                disconnected.add(ws)

        self._ws_clients -= disconnected

    @property
    def client_count(self) -> int:
        return len(self._ws_clients)
