"""CLI presentation using Rich (tables, panels, prompts)."""

from __future__ import annotations

import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, List, Mapping, Optional, TYPE_CHECKING

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from utils.constants import APP_VERSION, DEVELOPER_NAME
from utils.module_groups import group_kernel_modules

if TYPE_CHECKING:
    from modules.compiler import BuildProgressSnapshot

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


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class BuildProgressDisplay:
    """Live terminal panel for kernel compilation (make output stays in log file)."""

    def __init__(self, log_path: Path, make_target: str) -> None:
        self.log_path = log_path
        self.make_target = make_target
        self._snapshot: Optional["BuildProgressSnapshot"] = None
        self._live: Optional[Live] = None
        self._last_refresh = 0.0
        self._finished = False

    def _render(self) -> Panel:
        snap = self._snapshot
        if snap is None:
            body = Text("Starting …", style="dim")
        else:
            pct = min(100.0, max(0.0, snap.percent))
            bar_width = 36
            filled = int(bar_width * pct / 100.0)
            bar = f"[cyan]{'█' * filled}[/][dim]{'░' * (bar_width - filled)}[/]"
            eta = "—"
            if snap.eta_seconds is not None and not self._finished:
                eta = _format_duration(snap.eta_seconds)

            lines = Table.grid(padding=(0, 0))
            lines.add_row(f"[bold cyan]{snap.phase_label}[/]")
            lines.add_row(f"{bar} [bold]{pct:5.1f}%[/]")
            lines.add_row(
                Text.from_markup(
                    f"[dim]Elapsed[/] {_format_duration(snap.elapsed_seconds)}"
                    f"  [dim]·[/]  [dim]ETA[/] {eta}"
                )
            )
            activity = snap.activity or "…"
            if len(activity) > 76:
                activity = activity[:73] + "…"
            lines.add_row(Text(activity, style="italic dim"))
            lines.add_row(
                Text.from_markup(
                    f"[dim]make {self.make_target}[/]  [dim]·[/]  "
                    f"[dim]log[/] {self.log_path}"
                )
            )
            body = lines

        title = "[bold green]Build complete[/]" if self._finished else "[bold]Kernel build[/]"
        border = "green" if self._finished else "cyan"
        return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))

    def update(self, snapshot: "BuildProgressSnapshot") -> None:
        now = time.time()
        if (
            not self._finished
            and self._snapshot is not None
            and now - self._last_refresh < 0.12
            and snapshot.percent < 99.0
        ):
            return
        self._last_refresh = now
        self._snapshot = snapshot
        if snapshot.phase == "finishing" and snapshot.percent >= 100.0:
            self._finished = True
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    @contextmanager
    def live(self) -> Iterator[Callable[["BuildProgressSnapshot"], None]]:
        if not sys.stderr.isatty():
            yield self._noop_update
            return
        console.print()
        with Live(self._render(), console=console, refresh_per_second=8, transient=False) as live:
            self._live = live
            try:
                yield self.update
            finally:
                self._live = None
                if self._snapshot is not None:
                    self._finished = True
                    live.update(self._render(), refresh=True)
        console.print()

    @staticmethod
    def _noop_update(_snapshot: "BuildProgressSnapshot") -> None:
        return


@contextmanager
def build_progress_display(log_path: Path, make_target: str) -> Iterator[Callable[["BuildProgressSnapshot"], None]]:
    display = BuildProgressDisplay(log_path, make_target)
    with display.live() as callback:
        yield callback


def print_error_block(title: str, message: str, hints: Optional[List[str]] = None) -> None:
    body = Text()
    body.append(message, style="red")
    if hints:
        body.append("\n\n", style="")
        body.append("Suggestions:\n", style="bold yellow")
        for h in hints:
            body.append(f"  • {h}\n", style="dim")
    console.print(Panel(body, title=f"[bold red]{title}[/]", border_style="red", box=box.ROUNDED))
