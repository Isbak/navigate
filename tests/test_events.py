from catalog.events import (
    Artifact,
    ScanEvent,
    ScanEventBus,
    ScanStats,
    ScanStatus,
)


def _artifact(status=ScanStatus.RAW, lifecycle=None):
    return Artifact(
        id="doc_abc123",
        path="/tmp/x.txt",
        filename="x.txt",
        file_type="txt",
        size_bytes=1,
        created_at="t",
        modified_at="t",
        sha256="abc",
        source_system="test",
        scan_status=status,
        last_scanned_at="t",
        first_seen_at="t",
        lifecycle=lifecycle or status,
    )


def test_bus_delivers_to_all_subscribers():
    bus = ScanEventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(("a", e.status)))
    bus.subscribe(lambda e: seen.append(("b", e.status)))
    bus.publish(ScanEvent(ScanStatus.RAW, _artifact()))
    assert ("a", ScanStatus.RAW) in seen
    assert ("b", ScanStatus.RAW) in seen


def test_status_filtered_subscription():
    bus = ScanEventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.status), statuses={ScanStatus.CHANGED})
    bus.publish(ScanEvent(ScanStatus.RAW, _artifact()))
    bus.publish(ScanEvent(ScanStatus.CHANGED, _artifact(ScanStatus.CHANGED)))
    assert seen == [ScanStatus.CHANGED]


def test_failing_subscriber_does_not_break_others():
    bus = ScanEventBus()
    seen = []

    def boom(_event):
        raise RuntimeError("subscriber failure")

    bus.subscribe(boom)
    bus.subscribe(lambda e: seen.append(e.status))
    bus.publish(ScanEvent(ScanStatus.RAW, _artifact()))
    assert seen == [ScanStatus.RAW]


def test_stats_counts_duplicate_and_lifecycle_independently():
    stats = ScanStats()
    # A brand-new file that is also a duplicate copy.
    stats.record(_artifact(status=ScanStatus.DUPLICATE, lifecycle=ScanStatus.RAW))
    assert stats.files_scanned == 1
    assert stats.new_files == 1
    assert stats.duplicate_files == 1


def test_stats_counts_deleted_separately():
    stats = ScanStats()
    stats.record(_artifact(status=ScanStatus.DELETED, lifecycle=ScanStatus.DELETED))
    assert stats.deleted_files == 1
    assert stats.files_scanned == 0
