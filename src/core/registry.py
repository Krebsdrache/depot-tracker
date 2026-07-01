"""Registry aller bekannten Depot-Provider.

Zentrale Stelle, an der neue Provider (Trade Republic, Bitget, MetaMask, …)
später einfach ergänzt werden. Die UI fragt hier nach, welche Depots es gibt
und welche davon einsatzbereit (konfiguriert) sind.
"""

from __future__ import annotations

from typing import Callable

from providers.base import Provider
from providers.binance import BinanceProvider

# Factories statt Instanzen: Provider werden bei Bedarf frisch erzeugt.
_PROVIDER_FACTORIES: list[Callable[[], Provider]] = [
    BinanceProvider,
]

GESAMT_ID = "gesamt"


def all_providers() -> list[Provider]:
    """Alle registrierten Provider (unabhängig davon, ob konfiguriert)."""
    return [factory() for factory in _PROVIDER_FACTORIES]


def enabled_providers() -> list[Provider]:
    """Nur die einsatzbereiten Provider (z. B. Binance mit gültigen Keys)."""
    return [provider for provider in all_providers() if provider.is_configured()]


def get_provider(provider_id: str) -> Provider | None:
    """Findet einen Provider anhand seiner ID."""
    for provider in all_providers():
        if provider.info().id == provider_id:
            return provider
    return None
