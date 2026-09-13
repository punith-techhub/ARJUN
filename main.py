"""Application entry point for the autonomous Telegram developer bot."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from agents.base import BaseAgent
from agents.coder import CoderAgent
from agents.orchestrator import Orchestrator
from agents.planner import PlannerAgent
from agents.reviewer import ReviewerAgent
from config.settings import get_settings
from services.github_service import GitHubService
from services.memory_service import MemoryService
from services.project_service import ProjectManager
from services.secret_service import SecretStore
from services.telegram_handler import TelegramHandler
from services.vercel_service import VercelService
from utils.audio import VoiceTranscriber

_START_TIME = time.monotonic()
logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal JSON health endpoint for Render / UptimeRobot pings."""

    def do_GET(self) -> None:
        uptime = int(time.monotonic() - _START_TIME)
        body = json.dumps(
            {"status": "alive", "uptime_seconds": uptime, "version": "arjun-2.0"},
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence default stderr logging for health pings."""


def _self_ping(url: str, interval: int = 600) -> None:
    """Background thread that pings the Render external URL to prevent sleep."""
    while True:
        time.sleep(interval)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                logger.info("Self-ping %s -> %s", url, resp.status)
        except Exception as exc:
            logger.warning("Self-ping failed: %s", exc)


def build_application() -> tuple[Application, BaseAgent, GitHubService]:
    """Construct the Telegram application and all dependency-injected services."""
    settings = get_settings()
    base_agent = BaseAgent(settings)
    planner = PlannerAgent(base_agent)
    coder = CoderAgent(base_agent)
    reviewer = ReviewerAgent(base_agent)
    github = GitHubService(settings)
    memory = MemoryService(settings.state_db_path, settings.github_repo)
    secrets = SecretStore(settings.state_db_path, settings.arjun_secret_key)
    project_manager = ProjectManager(settings, base_agent)
    vercel = VercelService(settings)
    orchestrator = Orchestrator(
        planner,
        coder,
        reviewer,
        github,
        vercel,
        memory,
        secrets,
        project_manager,
    )
    handler = TelegramHandler(settings, orchestrator, VoiceTranscriber(base_agent), memory=memory)

    async def shutdown(_: Application) -> None:
        """Close external clients before the polling event loop exits."""
        await base_agent.close()
        await github.close()
        await vercel.close()
        await memory.close()
        await secrets.close()

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_shutdown(shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", handler.start, block=False))
    application.add_handler(CommandHandler("help", handler.start, block=False))
    application.add_handler(CommandHandler("cancel", handler.cancel, block=False))
    application.add_handler(MessageHandler(filters.VOICE, handler.handle_voice, block=False))
    application.add_handler(MessageHandler(filters.Document.ALL, handler.handle_document, block=False))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler.handle_text, block=False))
    application.add_error_handler(handler.error_handler)
    return application, base_agent, github


def main() -> None:
    """Validate configuration and start long-polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Start the health-check HTTP server for Render
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", port), _HealthHandler).serve_forever(),
        daemon=True,
    ).start()
    logger.info("Health endpoint listening on port %d", port)

    # Start the self-ping keep-alive thread
    settings = get_settings()
    ping_url = settings.render_external_url
    if ping_url:
        threading.Thread(target=_self_ping, args=(ping_url,), daemon=True).start()
        logger.info("Self-ping keep-alive started for %s (every 10 min)", ping_url)

    # httpx logs full Telegram URLs at INFO, which would expose the bot token.
    # Keep provider failures in our own sanitized handlers instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    while True:
        try:
            application, base_agent, github = build_application()
            try:
                application.run_polling(
                    allowed_updates=["message"],
                    drop_pending_updates=True,
                    bootstrap_retries=10,
                )
                break
            finally:
                del base_agent, github
        except Exception as error:
            if "conflict" in str(error).lower():
                logging.getLogger(__name__).warning(
                    "Telegram polling conflict detected (previous deployment container is shutting down). "
                    "Waiting 6 seconds before reconnecting..."
                )
                time.sleep(6)
                continue
            raise


if __name__ == "__main__":
    main()
