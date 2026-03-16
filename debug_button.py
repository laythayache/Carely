#!/usr/bin/env python3
"""
Standalone Button Debug Monitor
Run this separately to capture and display all button press signals on port 9090
"""

import asyncio
import logging
import signal
import sys

from src.button_debug_server import ButtonDebugServer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Run the debug server."""
    server = ButtonDebugServer(host="0.0.0.0", port=9090)
    await server.start()

    logger.info("=" * 80)
    logger.info("🔘 Button Debug Monitor is running!")
    logger.info("=" * 80)
    logger.info("")
    logger.info("📊 Open your browser and go to:")
    logger.info("   → http://localhost:9090")
    logger.info("")
    logger.info("🔗 Also configure your button to send to BOTH endpoints:")
    logger.info("   → Carely (main):  http://192.168.1.104:8080/button")
    logger.info("   → Debug monitor:  http://192.168.1.104:9090/button")
    logger.info("")
    logger.info("Or redirect the button to point only to this debug server to see raw signals.")
    logger.info("")
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 80)

    # Handle shutdown
    loop = asyncio.get_event_loop()

    def handle_shutdown(signum, frame):
        logger.info("\nShutting down...")
        asyncio.create_task(server.stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("\nShutdown requested")
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
