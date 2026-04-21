"""
integrations/base.py – Abstract base class for all external API integrations.

To add a new integration:
  1. Subclass BaseIntegration.
  2. Implement test_connection() and on_p1_alert().
  3. Register it in registry.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseIntegration(ABC):
    """
    Base class for all external API integrations.

    Each subclass receives its own config dict (the value of
    settings["integrations"][name]) on construction.
    """

    #: Short identifier used as the dict key in settings["integrations"]
    name: str = ""

    #: Human-readable label shown in the Settings UI
    label: str = ""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """
        Attempt to authenticate and reach the remote API.
        Returns (success: bool, message: str).
        """

    @abstractmethod
    def on_p1_alert(self, alert) -> tuple[bool, str]:
        """
        Called when a P1 alert is accepted (after dedupe check).
        Returns (success: bool, message: str).
        """

    @staticmethod
    def default_config() -> dict[str, Any]:
        """Return default config values for this integration."""
        return {"enabled": False}
