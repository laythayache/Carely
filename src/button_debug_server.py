"""
Button Debug Server - Monitor and display exact signals from ESP8266 button.
Runs on port 9090 with a web UI to view all button press events in real-time.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType, ClientSession

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent.parent / "debug_ui"


class ButtonDebugServer:
    """Captures and displays button press signals."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9090):
        self.host = host
        self.port = port
        self._ws_clients = set()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._http_session: ClientSession | None = None
        self.signal_history = []
        self.max_history = 100

    async def start(self) -> None:
        """Start the debug server."""
        self._http_session = ClientSession()
        self._app = web.Application()
        self._app.router.add_get("/", self._index_handler)
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_post("/button", self._button_handler)
        self._app.router.add_static("/", WEB_DIR, show_index=False)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"Button Debug Server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Gracefully shutdown the server."""
        for ws in list(self._ws_clients):
            await ws.close()
        self._ws_clients.clear()
        if self._http_session:
            await self._http_session.close()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Button Debug Server stopped")

    async def _index_handler(self, request: web.Request) -> web.FileResponse:
        """Serve index.html."""
        return web.FileResponse(WEB_DIR / "index.html")

    async def _button_handler(self, request: web.Request) -> web.Response:
        """Capture button press signal and relay to Carely service."""
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            data = {}

        signal_info = {
            "timestamp": datetime.now().isoformat(),
            "ip": request.remote,
            "method": request.method,
            "path": str(request.path),
            "headers": dict(request.headers),
            "payload": data,
            "status": "received",
        }

        self.signal_history.append(signal_info)
        if len(self.signal_history) > self.max_history:
            self.signal_history.pop(0)

        logger.info(f"[DEBUG] Button signal from {request.remote}: {data}")

        # Broadcast to WebSocket clients
        await self._broadcast({
            "type": "button_signal",
            "signal": signal_info,
            "total_signals": len(self.signal_history),
        })

        # Relay button press to actual Carely service on port 8080
        await self._relay_to_carely(data, request.headers)

        return web.json_response({"status": "captured"})

    async def _relay_to_carely(self, payload: dict[str, Any], headers: dict) -> None:
        """Forward button press to the actual Carely service on port 8080."""
        if not self._http_session:
            logger.warning("[RELAY] HTTP session not initialized")
            return

        try:
            # Extract authorization header if present
            auth_header = headers.get("Authorization", headers.get("authorization", ""))
            relay_headers = {}
            if auth_header:
                relay_headers["Authorization"] = auth_header

            # Forward to Carely service
            async with self._http_session.post(
                "http://localhost:8080/button",
                json=payload,
                headers=relay_headers,
                timeout=2,
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[RELAY] Button forwarded to Carely (8080): {resp.status}")
                else:
                    logger.warning(f"[RELAY] Carely returned {resp.status}")
        except asyncio.TimeoutError:
            logger.warning("[RELAY] Timeout forwarding to Carely (8080)")
        except Exception as e:
            logger.error(f"[RELAY] Failed to forward button to Carely: {e}")

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)

        logger.info(f"WebSocket client connected: {request.remote}")

        # Send history on connect
        await ws.send_json({
            "type": "history",
            "signals": self.signal_history,
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    logger.debug(f"WebSocket message: {msg.data}")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._ws_clients.discard(ws)
            logger.info(f"WebSocket client disconnected: {request.remote}")

        return ws

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

        self._ws_clients -= disconnected
