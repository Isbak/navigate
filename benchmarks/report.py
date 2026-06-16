"""Report emitters for the benchmark suite: JSON file and a console table."""

from __future__ import annotations

import json
from pathlib import Path


def build_report(results: list, metadata: dict) -> dict:
    """Assemble the serializable report document."""

    return {
        "metadata": metadata,
        "passed": all(r.passed for r in results),
        "stages": {r.stage: r.as_dict() for r in results},
    }


def write_json(report: dict, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _format_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_console(report: dict) -> str:
    """Render a human-readable table. Uses ``rich`` when available."""

    metadata = report["metadata"]
    header = (
        f"Benchmark suite  |  provider={metadata.get('provider')}  "
        f"|  {'PASS' if report['passed'] else 'FAIL'}"
    )

    try:
        import io

        from rich.console import Console
        from rich.table import Table

        # Render into a buffer (not straight to stdout) so the caller prints the
        # exported text exactly once.
        console = Console(record=True, width=100, file=io.StringIO())
        console.print(f"[bold]{header}[/bold]")
        for stage, data in report["stages"].items():
            status = "[green]PASS[/green]" if data["passed"] else "[red]FAIL[/red]"
            table = Table(title=f"{stage}  {status}", title_justify="left", show_lines=False)
            table.add_column("metric", style="cyan")
            table.add_column("value", justify="right")
            for key, value in data["quality"].items():
                table.add_row(key, _format_value(value))
            perf = data["performance"]
            if perf:
                table.add_row(
                    "[dim]perf[/dim]",
                    f"[dim]{perf.get('items_per_sec', 0)}/s, "
                    f"{perf.get('ms_per_item', 0)} ms/item[/dim]",
                )
            for failure in data["failures"]:
                table.add_row("[red]gate[/red]", f"[red]{failure}[/red]")
            if data["error"]:
                table.add_row("[red]error[/red]", f"[red]{data['error']}[/red]")
            console.print(table)
        return console.export_text()
    except ImportError:  # pragma: no cover - rich is a declared dependency
        return _render_plain(report, header)


def _render_plain(report: dict, header: str) -> str:
    lines = [header, "=" * len(header)]
    for stage, data in report["stages"].items():
        lines.append(f"\n[{stage}] {'PASS' if data['passed'] else 'FAIL'}")
        for key, value in data["quality"].items():
            lines.append(f"  {key:32} {_format_value(value)}")
        perf = data["performance"]
        if perf:
            lines.append(
                f"  {'perf':32} {perf.get('items_per_sec', 0)}/s, "
                f"{perf.get('ms_per_item', 0)} ms/item"
            )
        for failure in data["failures"]:
            lines.append(f"  GATE FAILED: {failure}")
        if data["error"]:
            lines.append(f"  ERROR: {data['error']}")
    return "\n".join(lines)


__all__ = ["build_report", "write_json", "render_console"]
