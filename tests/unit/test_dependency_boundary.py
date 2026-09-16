"""Real dependency containment tests, not a claim that ChromaDB is patched."""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from money.adapters.native_process import (
    BoundedNativeRunner,
    NativeProcessPolicy,
    ProviderEscapeDenied,
)


class ProbeResult(BaseModel):
    reached: bool = False


@dataclass(frozen=True)
class DependencyProbe:
    module: str
    symbol: str | None = None

    def __call__(self) -> ProbeResult:
        module: Any = importlib.import_module(self.module)
        if self.symbol:
            getattr(module, self.symbol)()
        return ProbeResult(reached=True)


def _bind_listener() -> ProbeResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
    return ProbeResult(reached=True)


@pytest.mark.parametrize("factory", [
    "Client", "PersistentClient", "EphemeralClient", "HttpClient", "AsyncHttpClient", "CloudClient",
])
def test_native_process_cannot_construct_real_chroma_client(factory: str) -> None:
    runner = BoundedNativeRunner(
        DependencyProbe("chromadb", factory), ProbeResult, NativeProcessPolicy(timeout_seconds=20),
    )
    with pytest.raises(ProviderEscapeDenied, match="ChromaDB storage/embedding"):
        runner()


@pytest.mark.parametrize("module", [
    "chromadb.server", "chromadb.server.fastapi", "chromadb.api.fastapi",
    "chromadb.api.async_fastapi", "chromadb.api.rust", "chromadb.api.segment",
])
def test_native_process_cannot_load_chroma_server_or_backend(module: str) -> None:
    runner = BoundedNativeRunner(
        DependencyProbe(module), ProbeResult, NativeProcessPolicy(timeout_seconds=20),
    )
    with pytest.raises(ProviderEscapeDenied, match="ChromaDB server/backend"):
        runner()


@pytest.mark.parametrize("embedding", [
    "SentenceTransformerEmbeddingFunction", "HuggingFaceEmbeddingFunction",
    "OpenAIEmbeddingFunction", "ONNXMiniLM_L6_V2",
])
def test_native_process_cannot_construct_chroma_embedding_provider(embedding: str) -> None:
    runner = BoundedNativeRunner(
        DependencyProbe("chromadb.utils.embedding_functions", embedding),
        ProbeResult, NativeProcessPolicy(timeout_seconds=20),
    )
    with pytest.raises(ProviderEscapeDenied, match="ChromaDB storage/embedding"):
        runner()


def test_native_process_cannot_open_listener_even_on_loopback() -> None:
    runner = BoundedNativeRunner(_bind_listener, ProbeResult, NativeProcessPolicy(timeout_seconds=20))
    with pytest.raises(ProviderEscapeDenied, match="listener capability denied"):
        runner()


def test_captured_chroma_constructor_aliases_cannot_bypass_restrictions() -> None:
    # Use a disposable interpreter: these restrictions intentionally cannot be
    # removed from a running native process and must not alter the pytest host.
    script = """
from chromadb import Client, PersistentClient
from chromadb.config import System, Settings
from chromadb.api.client import Client as DirectClient
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from money.adapters.native_isolation import install_dependency_restrictions
install_dependency_restrictions(PermissionError)
for constructor in (Client, PersistentClient, DirectClient, SentenceTransformerEmbeddingFunction):
    try:
        constructor()
    except PermissionError as error:
        assert 'ChromaDB' in str(error)
    else:
        raise AssertionError('captured constructor escaped isolation')
try:
    System(Settings())
except PermissionError:
    pass
else:
    raise AssertionError('direct System escaped isolation')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], timeout=30, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_control_plane_locked_export_excludes_native_cio_but_research_and_dev_keep_it(
    tmp_path: Path,
) -> None:
    command = [
        "uv", "export", "--frozen", "--offline", "--no-emit-project", "--no-hashes", "--quiet",
    ]
    environment = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    exports = {}
    for name, arguments in (
        ("control", ["--no-dev"]),
        ("research", ["--no-dev", "--extra", "research"]),
        ("development", []),
    ):
        result = subprocess.run(
            [*command, *arguments], timeout=30, capture_output=True, text=True, env=environment,
        )
        assert result.returncode == 0, result.stderr
        exports[name] = result.stdout
    for dependency in ("crewai==", "chromadb==", "crewai-tools=="):
        assert dependency not in exports["control"]
        assert dependency in exports["research"]
        assert dependency in exports["development"]


def test_control_services_import_without_native_research_packages(tmp_path: Path) -> None:
    script = """
import importlib.abc
import sys
class NoResearchRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'crewai', 'crewai_tools', 'chromadb'}:
            raise ImportError('Research runtime is not installed in the control plane')
        return None
sys.meta_path.insert(0, NoResearchRuntime())
import money.api.app
import money.accounts.email_worker
import money.product.worker
assert not any(name.split('.')[0] in {'crewai', 'crewai_tools', 'chromadb'} for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], timeout=30, capture_output=True, text=True,
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
            "MONEY_ENV": "test", "MONEY_DATABASE_URL": "sqlite:///:memory:",
        },
    )
    assert result.returncode == 0, result.stderr[-2000:]
