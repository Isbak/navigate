import json

from catalog.db import connect
from catalog.links import discover_links
from catalog.links import repository as repo
from catalog.links.config import LinkConfig


def _write_cache(cache_dir, artifact_id, raw_links):
    artifact_cache = cache_dir / artifact_id
    artifact_cache.mkdir(parents=True, exist_ok=True)
    (artifact_cache / "links.json").write_text(json.dumps(raw_links), encoding="utf-8")
    (artifact_cache / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "extracted_at": "2026-01-01T00:00:00"}),
        encoding="utf-8",
    )


def test_discovers_and_normalizes_links(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(
        cache,
        "doc_abc",
        [
            {"raw_url": "https://github.com/acme/repo/pull/3", "anchor_text": "PR"},
            {"raw_url": "https://example.com/p?utm_source=x&id=1", "anchor_text": None},
        ],
    )

    stats = discover_links(db, cache, LinkConfig.empty())
    assert stats.artifacts_processed == 1
    assert stats.links_found == 2
    assert stats.links_new == 2

    with connect(db) as conn:
        rows = {r["raw_url"]: r for r in repo.all_links(conn)}
    pr = rows["https://github.com/acme/repo/pull/3"]
    assert pr["target_system"] == "github"
    assert pr["target_type"] == "pull_request"
    assert pr["link_kind"] == "external"
    web = rows["https://example.com/p?utm_source=x&id=1"]
    # Tracking parameter stripped during normalization.
    assert web["normalized_url"] == "https://example.com/p?id=1"


def test_duplicate_detection_updates_instead_of_inserting(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_x", [{"raw_url": "https://example.com/a", "anchor_text": "A"}])

    first = discover_links(db, cache, LinkConfig.empty())
    assert first.links_new == 1
    assert first.links_updated == 0

    # Same artifact + normalized_url + anchor -> dedup hit on the second run.
    second = discover_links(db, cache, LinkConfig.empty())
    assert second.links_new == 0
    assert second.links_updated == 1

    with connect(db) as conn:
        assert repo.count_links(conn) == 1
        row = repo.all_links(conn)[0]
        assert row["last_seen_at"] >= row["discovered_at"]


def test_stale_links_when_link_disappears(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(
        cache,
        "doc_y",
        [
            {"raw_url": "https://example.com/keep", "anchor_text": None},
            {"raw_url": "https://example.com/gone", "anchor_text": None},
        ],
    )
    discover_links(db, cache, LinkConfig.empty())

    # Re-extract with one link removed.
    _write_cache(cache, "doc_y", [{"raw_url": "https://example.com/keep", "anchor_text": None}])
    stats = discover_links(db, cache, LinkConfig.empty())
    assert stats.links_removed == 1

    with connect(db) as conn:
        stale = repo.stale_links(conn)
        # Stale links are flagged, never deleted.
        assert repo.count_links(conn) == 2
    assert len(stale) == 1
    assert stale[0]["normalized_url"] == "https://example.com/gone"


def test_reappearing_link_is_reactivated(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_z", [{"raw_url": "https://example.com/x", "anchor_text": None}])
    discover_links(db, cache, LinkConfig.empty())

    _write_cache(cache, "doc_z", [])  # link disappears -> stale
    discover_links(db, cache, LinkConfig.empty())
    with connect(db) as conn:
        assert repo.all_links(conn)[0]["status"] == "STALE"

    _write_cache(cache, "doc_z", [{"raw_url": "https://example.com/x", "anchor_text": None}])
    discover_links(db, cache, LinkConfig.empty())
    with connect(db) as conn:
        assert repo.all_links(conn)[0]["status"] == "ACTIVE"


def test_discover_single_artifact(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", [{"raw_url": "https://example.com/a", "anchor_text": None}])
    _write_cache(cache, "doc_b", [{"raw_url": "https://example.com/b", "anchor_text": None}])

    stats = discover_links(db, cache, LinkConfig.empty(), artifact_id="doc_a")
    assert stats.artifacts_processed == 1
    with connect(db) as conn:
        rows = repo.all_links(conn)
    assert {r["source_artifact_id"] for r in rows} == {"doc_a"}


def test_records_link_scan_run(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", [{"raw_url": "https://example.com/a", "anchor_text": None}])
    discover_links(db, cache, LinkConfig.empty())

    with connect(db) as conn:
        run = repo.latest_link_scan_run(conn)
    assert run["artifacts_processed"] == 1
    assert run["links_found"] == 1
    assert run["completed_at"] is not None


def test_internal_domain_config_marks_link_internal(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", [{"raw_url": "https://github.com/acme/repo", "anchor_text": None}])

    cfg = LinkConfig(internal_domains=("github.com/acme",))
    discover_links(db, cache, cfg)
    with connect(db) as conn:
        assert repo.all_links(conn)[0]["link_kind"] == "internal"
