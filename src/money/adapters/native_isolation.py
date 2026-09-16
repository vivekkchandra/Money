"""Disable unused vector-store capabilities inside Money's native child process.

CrewAI currently requires ChromaDB for import-time types, although Money never
uses its memory or knowledge store. This containment is not a vulnerability
patch or an arbitrary-code sandbox. Host isolation and unsuppressed dependency
audits remain release requirements.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from collections.abc import Sequence
from types import ModuleType
from typing import Any, NoReturn


class _ForbiddenChromaModules(importlib.abc.MetaPathFinder):
    """Prevent HTTP server/backend loading; harmless import-time types remain."""

    prefixes = (
        "chromadb.server",
        "chromadb.api.fastapi",
        "chromadb.api.async_fastapi",
        "chromadb.api.rust",
        "chromadb.api.segment",
    )

    def __init__(self, error: type[PermissionError]) -> None:
        self.error = error

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if any(fullname == prefix or fullname.startswith(prefix + ".")
               for prefix in self.prefixes):
            raise self.error("native ChromaDB server/backend capability denied")
        return None


def install_dependency_restrictions(error: type[PermissionError]) -> None:
    """Permanently constrain this disposable process, including captured aliases.

    Mutating the actual classes, rather than only exported factory names, also
    protects factory aliases imported before the guard. No upstream source is
    changed. An absent optional runtime does not install or download anything.
    """
    if importlib.util.find_spec("chromadb") is None:
        return
    blocker = _ForbiddenChromaModules(error)
    if any(name == prefix or name.startswith(prefix + ".")
           for name in sys.modules for prefix in blocker.prefixes):
        raise error("native ChromaDB backend was loaded before capability isolation")
    sys.meta_path.insert(0, blocker)
    chroma: Any = importlib.import_module("chromadb")
    config: Any = importlib.import_module("chromadb.config")

    def denied(*args: Any, **kwargs: Any) -> NoReturn:
        raise error("native ChromaDB storage/embedding capability denied")

    # Every supported client factory eventually needs a System. Block that
    # concrete class as well as public factories so captured aliases also fail.
    config.System.__init__ = denied
    for name in ("Client", "PersistentClient", "EphemeralClient", "HttpClient",
                 "AsyncHttpClient", "CloudClient"):
        if hasattr(chroma, name):
            setattr(chroma, name, denied)
    for module_name in ("chromadb.api.client", "chromadb.api.async_client",
                        "chromadb.api.shared_system_client"):
        module: Any = importlib.import_module(module_name)
        for name in ("Client", "AsyncClient", "SharedSystemClient"):
            candidate = getattr(module, name, None)
            if isinstance(candidate, type):
                candidate.__init__ = denied  # type: ignore[misc]

    # Importing Chroma eagerly imports its embedding implementations. They must
    # never load a downloaded model, evaluate remote code, or invoke a provider.
    prefix = "chromadb.utils.embedding_functions"
    for name, module in tuple(sys.modules.items()):
        if module is None or not (name == prefix or name.startswith(prefix + ".")):
            continue
        for candidate in tuple(vars(module).values()):
            if not isinstance(candidate, type) or not candidate.__module__.startswith(prefix):
                continue
            for method in ("__call__", "build_from_config"):
                if method in candidate.__dict__:
                    setattr(candidate, method, denied)
            if "__init__" in candidate.__dict__:
                candidate.__init__ = denied  # type: ignore[misc]
