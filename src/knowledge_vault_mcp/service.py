"""Wires vault, index and retrieval together and runs the background sync loop."""

import logging
import threading
import time
from dataclasses import asdict

from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.index.db import IndexDB
from knowledge_vault_mcp.index.embedder import Embedder, make_embedder
from knowledge_vault_mcp.index.indexer import Indexer, IndexStats
from knowledge_vault_mcp.retrieval.search import Retriever
from knowledge_vault_mcp.vault import GitError, Vault

log = logging.getLogger(__name__)


class VaultService:
    def __init__(self, settings: Settings, embedder: Embedder | None = None):
        self.settings = settings
        self.vault = Vault(settings)
        self.db = IndexDB(settings.db_path)
        self.embedder = embedder or make_embedder(settings.embedding_model, settings.model_cache_dir)
        self.indexer = Indexer(settings, self.db, self.vault, self.embedder)
        self.retriever = Retriever(self.db, self.embedder, settings.inbox_dir)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_sync: float | None = None
        self.last_error: str | None = None
        self.last_stats: IndexStats | None = None

    # ---- lifecycle ----
    def start(self) -> None:
        """Clone, sync and index in a background thread, then keep syncing every SYNC_INTERVAL."""
        self._thread = threading.Thread(target=self._run, name="vault-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def _run(self) -> None:
        try:
            self.vault.open()
        except Exception as e:
            log.exception("cannot open vault")
            self.last_error = f"cannot open vault: {e}"
        self.sync_and_index()
        self._ready.set()
        interval = self.settings.sync_interval
        while interval > 0 and not self._stop.wait(interval):
            self.sync_and_index()

    def sync_and_index(self) -> IndexStats | None:
        """Pull from the remote, then bring the index up to date. Errors are logged, not raised."""
        error = None
        try:
            self.vault.sync()
        except GitError as e:
            log.warning("sync failed: %s", e)
            error = f"sync failed: {e}"
        try:
            self.last_stats = self.indexer.update()
        except Exception as e:
            log.exception("indexing failed")
            error = f"indexing failed: {e}"
        self.last_sync = time.time()
        self.last_error = error
        return self.last_stats

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "notes": self.db.note_count(),
            "chunks": self.db.chunk_count(),
            "indexed_commit": self.db.get_meta("indexed_commit"),
            "embedding_model": self.embedder.name,
            "last_sync": int(self.last_sync) if self.last_sync else None,
            "last_error": self.last_error,
            "last_index_run": asdict(self.last_stats) if self.last_stats else None,
        }
