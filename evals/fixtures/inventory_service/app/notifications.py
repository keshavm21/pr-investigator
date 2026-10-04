"""Outgoing email notifications."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str = "") -> None:
    # Delivery is handled by the mail relay; this service only records the request.
    logger.info("email to=%s subject=%s", to, subject)
