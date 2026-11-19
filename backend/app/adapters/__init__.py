"""Registar adaptera.

Svaki modul u ovom paketu koji definira podklasu `Adapter` s atributom
`code` automatski se registrira. Novi trgovac = nova datoteka, bez
diranja ostatka koda.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import Adapter, AdapterError, FormatChanged, PriceRow, RawFile, StoreInfo, StorePrices

_SKIP = {"base", "csvutil"}
_registry: dict[str, type[Adapter]] = {}


def _load() -> None:
    if _registry:
        return
    for mod in pkgutil.iter_modules(__path__):
        if mod.name in _SKIP or mod.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Adapter) and obj is not Adapter and not inspect.isabstract(obj):
                if obj.__module__ == module.__name__:
                    _registry[obj.code] = obj


def all_adapters() -> dict[str, type[Adapter]]:
    _load()
    return dict(sorted(_registry.items()))


def default_adapters() -> list[str]:
    return [c for c, a in all_adapters().items() if a.enabled_by_default]


def get_adapter(code: str) -> type[Adapter]:
    _load()
    try:
        return _registry[code]
    except KeyError:
        raise KeyError(f"Nepoznat trgovac {code!r}; dostupni: {sorted(_registry)}") from None


__all__ = [
    "Adapter", "AdapterError", "FormatChanged", "PriceRow", "RawFile", "StoreInfo",
    "StorePrices", "all_adapters", "default_adapters", "get_adapter",
]
