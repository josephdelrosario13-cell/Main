"""
Alert/notification system.

Supports Discord webhooks for real-time trade notifications.
Easily extensible for Telegram, email, SMS, etc.
"""

import json
import logging
from typing import Optional

import requests

from spx_bot.config import AppConfig, config

logger = logging.getLogger(__name__)


class AlertManager:
    """Send trade alerts via configured channels."""

    def __init__(self, cfg: AppConfig = config):
        self.cfg = cfg

    def send(self, title: str, message: str):
        """Send an alert through all enabled channels."""
        logger.info("ALERT [%s]: %s", title, message)

        if self.cfg.logging.enable_discord_alerts:
            self._send_discord(title, message)

    def _send_discord(self, title: str, message: str):
        """Send a Discord webhook notification."""
        webhook_url = self.cfg.logging.discord_webhook_url
        if not webhook_url:
            return

        payload = {
            "embeds": [
                {
                    "title": f"SPX 0DTE Bot: {title}",
                    "description": message,
                    "color": 0x00FF00 if "Opened" in title or "Profit" in title else 0xFF6600,
                }
            ]
        }

        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=5,
            )
            if resp.status_code not in (200, 204):
                logger.warning("Discord alert failed: %s", resp.status_code)
        except Exception as e:
            logger.warning("Discord alert error: %s", e)
