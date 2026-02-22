"""
Health check endpoint and systemd watchdog notifier.
"""

import asyncio
import logging
import time
from aiohttp import web

logger = logging.getLogger(__name__)


class HealthServer:
    """
    HTTP health check endpoint on a dedicated port.
    Also sends systemd watchdog notifications.
    """

    def __init__(self, port: int, watchdog_interval_s: int = 15):
        self.port = port
        self.watchdog_interval_s = watchdog_interval_s
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._start_time = time.monotonic()
        self._status: dict = {"state": "starting"}

    def update_status(self, state: str, **kwargs) -> None:
        """Update the health status reported by the endpoint."""
        self._status = {"state": state, "uptime_s": int(time.monotonic() - self._start_time), **kwargs}

    async def start(self) -> None:
        """Start health HTTP server and watchdog notifier."""
        self._app = web.Application()
        self._app.router.add_get("/health", self._health_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info(f"Health server started on port {self.port}")

        # Notify systemd that we're ready
        try:
            import sdnotify
            n = sdnotify.SystemdNotifier()
            n.notify("READY=1")
            logger.info("Notified systemd: READY")
        except ImportError:
            pass

    async def stop(self) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._runner:
            await self._runner.cleanup()

    async def _health_handler(self, request: web.Request) -> web.Response:
        return web.json_response(self._status)

    async def _watchdog_loop(self) -> None:
        """Periodically notify systemd watchdog."""
        try:
            import sdnotify
            n = sdnotify.SystemdNotifier()
        except ImportError:
            return

        while True:
            try:
                n.notify("WATCHDOG=1")
                await asyncio.sleep(self.watchdog_interval_s)
            except asyncio.CancelledError:
                break
