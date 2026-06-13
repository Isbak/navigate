from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .db import connect, init_db, replace_links, upsert_artifact
from .extractors import get_extractor
from .hashing import document_id, sha256_file
import hashlib
from .links import classify_target_system, classify_target_type, extract_links_from_text

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".pdf", ".md", ".txt"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_excluded(path: Path, patterns: list[str]) -> bool:
    value = path.as_posix()
    return any(fnmatch.fnmatch(value, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def iter_documents(source: Path, exclude: list[str]):
    if not source.exists():
        LOGGER.warning("Source path does not exist: %s", source)
        return
    for path in source.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and not is_excluded(path, exclude):
            yield path


def extract_text(path: Path) -> str:
    if path.suffix.lower() in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    extractor = get_extractor(path)
    if extractor is None:
        return ""
    return extractor.extract_text(path)


def unique_document_id(digest: str, path: Path, db_path: str | Path) -> str:
    """Return a stable artifact ID while allowing same-content duplicate files.

    The first observed file for a SHA-256 receives doc_<first_12_chars>. Additional
    files with identical content receive a deterministic path suffix so the
    artifacts table can keep one row per source path and duplicates remain
    queryable by sha256.
    """
    base_id = document_id(digest)
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT path FROM artifacts WHERE id = ?", (base_id,)).fetchone()
    if row is None or row["path"] == str(path):
        return base_id
    path_digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{base_id}_{path_digest}"


def scan_file(path: str | Path, source_system: str = "local_laptop", db_path: str | Path = "data/catalog.sqlite", cache_dir: str | Path = "cache") -> str:
    path = Path(path).expanduser().resolve()
    digest = sha256_file(path)
    artifact_id = unique_document_id(digest, path, db_path)
    stat = path.stat()
    scanned_at = utc_now()
    artifact = {
        "id": artifact_id,
        "path": str(path),
        "filename": path.name,
        "file_type": path.suffix.lower().lstrip("."),
        "size_bytes": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": digest,
        "source_system": source_system,
        "scan_status": "indexed",
        "last_scanned_at": scanned_at,
    }
    text = ""
    try:
        text = extract_text(path)
    except Exception:
        LOGGER.exception("Text extraction failed for %s", path)
        artifact["scan_status"] = "metadata_only"

    artifact_cache = Path(cache_dir) / artifact_id
    artifact_cache.mkdir(parents=True, exist_ok=True)
    (artifact_cache / "extracted.txt").write_text(text, encoding="utf-8")

    links = []
    for link in extract_links_from_text(text):
        url = str(link["target_url"])
        links.append({
            "source_artifact_id": artifact_id,
            "target_url": url,
            "anchor_text": link.get("anchor_text"),
            "target_system": classify_target_system(url),
            "target_type": classify_target_type(url),
            "discovered_at": scanned_at,
        })

    init_db(db_path)
    with connect(db_path) as conn:
        upsert_artifact(conn, artifact)
        replace_links(conn, artifact_id, links)
    LOGGER.info("Indexed %s as %s", path, artifact_id)
    return artifact_id


def scan(config_path: str | Path = "config/sources.yml", db_path: str | Path = "data/catalog.sqlite", cache_dir: str | Path = "cache") -> int:
    cfg = load_config(config_path)
    count = 0
    for source in cfg.sources:
        root = Path(source.path).expanduser()
        for document in iter_documents(root, cfg.exclude) or []:
            scan_file(document, source.source_system, db_path, cache_dir)
            count += 1
    return count
