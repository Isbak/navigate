from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import load_config
from .scanner import SUPPORTED_EXTENSIONS, build_default_scanner

LOGGER = logging.getLogger(__name__)


class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, changes: queue.Queue[tuple[Path, str]], delay: float) -> None:
        self.changes = changes
        self.delay = delay
        self.pending: dict[Path, tuple[float, str]] = {}
        threading.Thread(target=self._flush_loop, daemon=True).start()

    def on_modified(self, event):
        self._record(event)

    def on_created(self, event):
        self._record(event)

    def _record(self, event) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            self.pending[path] = (time.monotonic(), "local_laptop")

    def _flush_loop(self) -> None:
        while True:
            now = time.monotonic()
            for path, (seen, source_system) in list(self.pending.items()):
                if now - seen >= self.delay:
                    self.pending.pop(path, None)
                    self.changes.put((path, source_system))
            time.sleep(0.5)


def watch(config_path: str | Path = "config/sources.yml", db_path: str | Path = "data/catalog.sqlite", cache_dir: str | Path = "cache", debounce: float = 2.0) -> None:
    cfg = load_config(config_path)
    scanner = build_default_scanner(db_path, cache_dir)
    changes: queue.Queue[tuple[Path, str]] = queue.Queue()
    observer = Observer()
    handler = DebouncedHandler(changes, debounce)
    for source in cfg.sources:
        path = Path(source.path).expanduser()
        if path.exists():
            observer.schedule(handler, str(path), recursive=True)
            LOGGER.info("Watching %s", path)
    observer.start()
    try:
        while True:
            path, source_system = changes.get()
            if path.exists():
                scanner.scan_path(path, source_system)
    finally:
        observer.stop()
        observer.join()
