"""Shared helpers for the CLI command modules.

These small utilities are used across several command groups; keeping them in one
place avoids importing them from the (now thin) top-level ``cli`` module.
"""

from __future__ import annotations

import logging


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def print_stats(stats: dict) -> None:
    print(f"Files scanned: {stats['files_scanned']}")
    print(f"New files: {stats['new_files']}")
    print(f"Modified files: {stats['changed_files']}")
    print(f"Deleted files: {stats['deleted_files']}")
    print(f"Duplicates: {stats['duplicate_files']}")


def fmt(n: int) -> str:
    return f"{n:,}"


__all__ = ["configure_logging", "print_stats", "fmt"]
