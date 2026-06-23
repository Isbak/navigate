"""CLI command modules.

Each module exposes a ``register(sub)`` function that adds its argparse
subparsers and attaches a handler to each via ``set_defaults(func=...)``. The
top-level :mod:`catalog.cli` wires them together; dispatch is uniform because it
just calls ``args.func(args)``.
"""

from __future__ import annotations

import argparse

from . import (
    benchmark,
    catalog,
    doctor,
    knowledge,
    links,
    rdf,
    review,
    semantic,
    serve,
)

# Ordered so ``catalog --help`` lists commands grouped roughly by pipeline stage.
_MODULES = [
    catalog,
    serve,
    links,
    semantic,
    knowledge,
    review,
    rdf,
    doctor,
    benchmark,
]


def register_all(sub: argparse._SubParsersAction) -> None:
    """Register every command module's subparsers on ``sub``."""

    for module in _MODULES:
        module.register(sub)


__all__ = ["register_all"]
