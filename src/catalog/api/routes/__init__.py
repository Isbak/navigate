"""REST API route modules.

Each module owns one resource group and exposes an APIRouter named ``router``.
:func:`catalog.api.app.create_app` includes them all under the ``/api`` prefix.
"""

from __future__ import annotations

from . import (
    artifacts,
    ask,
    evidence,
    governance,
    graph,
    health,
    jobs,
    knowledge,
    links,
    relationships,
)

ROUTERS = (
    health.router,
    artifacts.router,
    links.router,
    knowledge.router,
    relationships.router,
    evidence.router,
    governance.router,
    graph.router,
    ask.router,
    jobs.router,
)

__all__ = ["ROUTERS"]
