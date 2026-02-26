"""Fusion strategy plugin registry for the normalization layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import DerivedObservation, NormalizedObservation


class FusionPlugin(Protocol):
    """Contract for signal-fusion plugins."""

    plugin_id: str
    plugin_api_version: int
    supported_kinds: tuple[str, ...]

    def derive(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation: ...


@dataclass(frozen=True)
class FusionPluginDescriptor:
    plugin_id: str
    plugin_api_version: int
    supported_kinds: tuple[str, ...]


class NormalizationFusionRegistry:
    """Registry that resolves and executes fusion strategies/plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, FusionPlugin] = {}

    def register(self, plugin: FusionPlugin) -> None:
        plugin_id = str(getattr(plugin, "plugin_id", "")).strip()
        if not plugin_id:
            raise ValueError("Fusion plugin must define a non-empty plugin_id")
        if plugin_id in self._plugins:
            raise ValueError(f"Fusion plugin already registered: {plugin_id}")
        self._plugins[plugin_id] = plugin

    def get(self, plugin_id: str) -> FusionPlugin:
        plugin = self._plugins.get(str(plugin_id))
        if plugin is None:
            raise KeyError(f"Fusion plugin not found: {plugin_id}")
        return plugin

    def descriptors(self) -> list[FusionPluginDescriptor]:
        return [
            FusionPluginDescriptor(
                plugin_id=plugin.plugin_id,
                plugin_api_version=int(plugin.plugin_api_version),
                supported_kinds=tuple(plugin.supported_kinds),
            )
            for plugin in self._plugins.values()
        ]

    def derive(
        self,
        *,
        plugin_id: str,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation:
        plugin = self.get(plugin_id)
        if tuple(plugin.supported_kinds) and kind not in plugin.supported_kinds:
            raise ValueError(f"Fusion plugin '{plugin_id}' does not support kind '{kind}'")
        return plugin.derive(
            kind=kind,
            inputs=list(inputs),
            strategy_cfg=dict(strategy_cfg or {}),
            context=dict(context or {}),
        )

