"""CLI presentation using Rich (tables, panels, prompts)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from utils.constants import APP_VERSION, DEVELOPER_NAME
from utils.module_groups import group_kernel_modules

console = Console()


def banner() -> None:
    title = Text()
    title.append("GetKernel", style="bold bright_cyan")
    title.append("\n", style="")
    title.append(
        "Linux kernel build helper for Debian-based systems",
        style="dim italic",
    )
    title.append("\n", style="")
    title.append(f"v{APP_VERSION}", style="cyan")
    title.append(" · ", style="dim")
    title.append(DEVELOPER_NAME, style="dim cyan")
    console.print()
    console.print(
        Panel(
            title,
            border_style="bright_cyan",
            box=box.DOUBLE_EDGE,
            padding=(1, 3),
            width=min(72, console.width),
        ),
        justify="center",
    )
    console.print()


def print_interactive_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Show running kernel, hardware, PCI summary, and loaded modules before wizard continues."""
    info = snapshot.get("info") or {}
    os_i = info.get("os") or {}
    hw = info.get("hardware") or {}
    cpu = hw.get("cpu") or {}
    mem = hw.get("memory") or {}
    disk = hw.get("disk") or {}

    cod = (os_i.get("codename") or "").strip()
    os_line = f"{os_i.get('name', '—')} {os_i.get('version', '')}"
    if cod:
        os_line += f" ({cod})"

    disk_ok = disk.get("ok")
    disk_style = "green" if disk_ok else "yellow"
    disk_note = "OK" if disk_ok else "low"

    sys_table = Table(
        title="[bold]System overview[/]",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=False,
        padding=(0, 1),
        width=min(88, console.width),
    )
    sys_table.add_column("Field", style="dim cyan", width=22)
    sys_table.add_column("Value")

    sys_table.add_row("Host", snapshot.get("hostname") or "—")
    sys_table.add_row("Running kernel", str(os_i.get("kernel", "—")))
    sys_table.add_row("OS", os_line)
    sys_table.add_row(
        "CPU",
        f"{cpu.get('model', '—')} — "
        f"{cpu.get('cores', '?')} cores / {cpu.get('threads', '?')} threads — "
        f"{cpu.get('architecture', '')}",
    )
    sys_table.add_row(
        "Memory",
        f"{mem.get('ram_gb', 0):.1f} GiB RAM — {mem.get('swap_gb', 0):.1f} GiB swap",
    )
    sys_table.add_row(
        "Disk (build area)",
        Text(
            f"{disk.get('free_gb', 0):.1f} GiB free — {disk_note}",
            style=disk_style,
        ),
    )
    console.print(sys_table)

    pci = snapshot.get("pci_summary") or []
    if pci:
        pci_table = Table(
            title="[bold]Notable devices[/] [dim](lspci)[/]",
            box=box.SIMPLE,
            border_style="dim",
            show_header=False,
            padding=(0, 1),
            width=min(88, console.width),
        )
        pci_table.add_column("Device")
        for p in pci:
            pci_table.add_row(f"[cyan]•[/] {p}")
        console.print(pci_table)
    else:
        console.print(
            Panel(
                "[dim]Skipped — install [bold]pciutils[/] or ensure [bold]lspci[/] is available.[/]",
                title="[dim]PCI[/]",
                border_style="dim",
                box=box.MINIMAL,
            )
        )

    mods = snapshot.get("loaded_modules") or []
    mod_total = int(snapshot.get("loaded_modules_total") or 0)
    if mods:
        wrap_w = min(84, max(40, console.width - 8))
        tree = Tree(
            f"[bold]Loaded kernel modules[/] [dim]({mod_total} total, by role)[/]",
            guide_style="dim cyan",
        )
        groups = group_kernel_modules(mods)
        for cat, cat_mods in groups:
            if not cat_mods:
                continue
            joined = ", ".join(cat_mods)
            wrapped = textwrap.wrap(
                joined,
                width=wrap_w,
                break_long_words=True,
                break_on_hyphens=False,
            )
            label = f"[cyan]{cat}[/] [dim]({len(cat_mods)})[/]"
            if not wrapped:
                tree.add(label + " [dim]—[/]")
                continue
            sub = tree.add(label)
            for line in wrapped:
                sub.add(f"[dim]{line}[/]")
        console.print(Panel(tree, border_style="dim", box=box.ROUNDED, padding=(0, 1)))
    else:
        console.print(
            Panel(
                "[dim]Could not read /proc/modules[/]",
                title="[bold]Modules[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    console.print()


def print_step(step: int, total: int, title: str) -> None:
    """Numbered wizard step heading."""
    console.print()
    step_bar = Text()
    for i in range(1, total + 1):
        if i > 1:
            step_bar.append(" ", style="dim")
        if i < step:
            step_bar.append("●", style="green")
        elif i == step:
            step_bar.append("●", style="bold bright_cyan")
        else:
            step_bar.append("○", style="dim")
    header = Text()
    header.append(f"Step {step}/{total}", style="bold bright_cyan")
    header.append(" — ", style="dim")
    header.append(title, style="bold white")
    console.print(Panel(Group(step_bar, Text(""), header), border_style="cyan", box=box.ROUNDED))
    console.print()


def _column_widths(
    columns: List[str], rows: List[Mapping[str, Any]]
) -> List[int]:
    widths = [len(c) for c in columns]
    for row in rows:
        for i, c in enumerate(columns):
            widths[i] = max(widths[i], len(str(row.get(c, ""))))
    return widths


def _kernel_type_style(kind: str) -> str:
    k = (kind or "").lower()
    if k == "stable":
        return "bold green"
    if k == "longterm":
        return "bold blue"
    if k == "mainline":
        return "bold yellow"
    if k == "other":
        return "dim"
    return "white"


def print_table(title: str, rows: Iterable[Mapping[str, Any]], columns: List[str]) -> None:
    row_list = list(rows)
    if not row_list:
        console.print(
            Panel(
                "[dim](empty)[/]",
                title=f"[bold]{title}[/]",
                border_style="dim",
                box=box.ROUNDED,
            )
        )
        console.print()
        return

    # Kernel list / version tables: richer layout when columns match known shapes
    if columns == ["#", "version", "type", "released"]:
        table = Table(
            title=f"[bold]{title}[/]",
            box=box.ROUNDED,
            border_style="bright_blue",
            header_style="bold cyan",
            show_lines=False,
            padding=(0, 1),
            width=min(96, console.width),
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Version", style="bold white")
        table.add_column("Type", justify="center")
        table.add_column("Released", style="dim")
        for row in row_list:
            ver = str(row.get("version", ""))
            kt = str(row.get("type", ""))
            rel = str(row.get("released", ""))
            table.add_row(
                str(row.get("#", "")),
                ver,
                Text(kt, style=_kernel_type_style(kt)),
                rel,
            )
        console.print(table)
        console.print()
        return

    if columns == ["version", "type", "released"]:
        table = Table(
            title=f"[bold]{title}[/]",
            box=box.ROUNDED,
            border_style="bright_blue",
            header_style="bold cyan",
            padding=(0, 1),
            width=min(96, console.width),
        )
        table.add_column("Version", style="bold white")
        table.add_column("Type", justify="center")
        table.add_column("Released", style="dim")
        for row in row_list:
            kt = str(row.get("type", ""))
            table.add_row(
                str(row.get("version", "")),
                Text(kt, style=_kernel_type_style(kt)),
                str(row.get("released", "")),
            )
        console.print(table)
        console.print()
        return

    if columns == ["item", "ok"]:
        table = Table(
            title=f"[bold]{title}[/]",
            box=box.SIMPLE,
            border_style="cyan",
            show_header=True,
            header_style="bold",
            padding=(0, 1),
            width=min(72, console.width),
        )
        table.add_column("Check")
        table.add_column("Status", justify="center")
        for row in row_list:
            ok = str(row.get("ok", "")).lower() in ("true", "yes")
            st = Text("✓ OK", style="bold green") if ok else Text("✗", style="bold red")
            table.add_row(str(row.get("item", "")), st)
        console.print(table)
        console.print()
        return

    if columns == ["field", "value"]:
        table = Table(
            title=f"[bold]{title}[/]",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold cyan",
            padding=(0, 1),
            width=min(88, console.width),
        )
        table.add_column("Field", style="dim")
        table.add_column("Value")
        for row in row_list:
            table.add_row(str(row.get("field", "")), str(row.get("value", "")))
        console.print(table)
        console.print()
        return

    # Fallback: plain aligned columns
    widths = _column_widths(columns, row_list)
    parts = [str(col).ljust(widths[i]) for i, col in enumerate(columns)]
    header = "  ".join(parts)
    lines = [header, "-" * len(header)]
    for row in row_list:
        line = "  ".join(
            str(row.get(c, "")).ljust(widths[i]) for i, c in enumerate(columns)
        )
        lines.append(line)
    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold]{title}[/]",
            border_style="dim",
            box=box.ROUNDED,
        )
    )
    console.print()


def print_build_success_summary(
    package_count: int,
    latest_dir: Path,
    build_log: Optional[Path] = None,
) -> None:
    """Short success message after a successful kernel package build."""
    body = Text()
    body.append("Build finished successfully.\n\n", style="bold green")
    body.append(f"Created {package_count} .deb package(s) under\n", style="white")
    body.append(str(latest_dir), style="cyan")
    if build_log is not None:
        body.append("\n\nFull build log:\n", style="dim")
        body.append(str(build_log), style="dim cyan")
    console.print()
    console.print(Panel(body, title="[bold green]Success[/]", border_style="green", box=box.ROUNDED))
    console.print()


def confirm(message: str, default: bool = False) -> bool:
    try:
        return Confirm.ask(message, default=default, console=console)
    except EOFError:
        return default


def print_build_step_summary(version: str) -> None:
    """Final wizard step: show selected kernel before confirm."""
    body = Text()
    body.append("Selected kernel: ", style="bold white")
    body.append(version, style="bold green")
    body.append(
        "\n\nThe tool will download sources, apply your running kernel configuration, "
        "and compile. This may take a long time.",
        style="dim",
    )
    console.print(
        Panel(
            body,
            title="[bold cyan]Ready to build[/]",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )


def prompt_kernel_selection(choice_count: int) -> Optional[int]:
    """
    Prompt for a kernel line number (1-based list).

    Returns 0-based index into the versions list, or None if the user quits (empty input).
    Raises ValueError if input is non-empty but not a valid index.
    """
    if choice_count < 1:
        raise ValueError("no choices")
    console.print()
    console.print(
        "[dim]Type a [bold]number[/bold] from [bold]1[/]–"
        f"[bold]{choice_count}[/] to select a kernel, or press [bold]Enter[/] to quit.[/dim]"
    )
    raw = Prompt.ask(
        "[bold bright_cyan]Kernel selection[/bold bright_cyan]",
        default="",
        show_default=False,
        console=console,
    )
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        num = int(raw)
    except ValueError:
        raise ValueError("not an integer") from None
    idx = num - 1
    if idx < 0 or idx >= choice_count:
        raise ValueError("out of range")
    return idx


def progress_download() -> Progress:
    return Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
    )


def print_error_block(title: str, message: str, hints: Optional[List[str]] = None) -> None:
    body = Text()
    body.append(message, style="red")
    if hints:
        body.append("\n\n", style="")
        body.append("Suggestions:\n", style="bold yellow")
        for h in hints:
            body.append(f"  • {h}\n", style="dim")
    console.print(Panel(body, title=f"[bold red]{title}[/]", border_style="red", box=box.ROUNDED))
