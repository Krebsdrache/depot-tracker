"""Gemeinsames Interface für alle Depot-Provider.

Ein Provider kapselt einen konkreten Broker/Wallet (Binance, Trade Republic, …)
und liefert seine Daten ausschließlich als neutrale Kern-Typen zurück. Welche
Features ein Depot in der UI erhält, ergibt sich aus seinen `capabilities()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.model import AssetClass, Capability, DepotSnapshot, Transaction


@dataclass(frozen=True)
class ProviderInfo:
    """Statische Kenndaten eines Providers für Auswahl und Anzeige."""

    id: str
    display_name: str
    asset_classes: frozenset[AssetClass] = field(default_factory=frozenset)


class Provider(ABC):
    """Basisklasse für alle Depot-Provider.

    Unterklassen bilden Rohdaten (API, CSV/PDF-Import, …) auf das neutrale
    Domänenmodell ab. `app.py` und die Aggregation kennen nur dieses Interface,
    nie die konkrete Broker-Implementierung.
    """

    @abstractmethod
    def info(self) -> ProviderInfo:
        """Kenndaten (ID, Anzeigename, Anlageklassen)."""

    @abstractmethod
    def capabilities(self) -> set[Capability]:
        """Vom Provider unterstützte Fähigkeiten (schaltet UI-Features frei)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """True, wenn der Provider einsatzbereit ist (z. B. Keys/Import vorhanden)."""

    @abstractmethod
    def load_snapshot(self) -> DepotSnapshot:
        """Aktuelle Bestände als neutraler Snapshot."""

    def load_transactions(self) -> list[Transaction]:
        """Neutrale Transaktionshistorie. Standard: leer (nicht jeder Provider liefert Historie)."""
        return []

    def supports(self, capability: Capability) -> bool:
        """Bequemer Test, ob eine Fähigkeit vorhanden ist."""
        return capability in self.capabilities()
