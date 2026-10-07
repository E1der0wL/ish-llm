"""Rich progress rendered by PTK, without a second terminal renderer."""

import asyncio
from io import StringIO

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.layout import UIContent, UIControl, Window
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Column


class ProgressControl(UIControl):
    def __init__(self, owner, notice):
        self.owner = owner
        self.notice = notice

    def create_content(self, width: int, height: int) -> UIContent:
        owner = self.owner
        tasks = owner.progress.tasks
        if not tasks:
            text = " ".join(self.notice().split())
            return UIContent(get_line=lambda _: [("class:hub.notice", text)] if text else [], line_count=1, show_cursor=False)
        owner.schedule_refresh()
        theme = owner.view.theme
        key = (width, theme)
        if key in owner._render_cache:
            line = owner._render_cache[key]
            return UIContent(get_line=lambda _: line, line_count=1, show_cursor=False)
        owner.bar.style = theme.muted
        owner.bar.complete_style = owner.bar.finished_style = owner.bar.pulse_style = theme.accent
        task = tasks[-1]
        task.fields["elapsed"] = owner.view.t.elapsed(task.elapsed)
        task.fields["label"] = task.description + (
            owner.view.t("progress_more", count=len(tasks) - 1) if len(tasks) > 1 else "")
        stream = StringIO()
        console = Console(file=stream, width=max(1, width), color_system="truecolor",
                          force_terminal=True, highlight=False)
        console.print(owner.progress.make_tasks_table([task]), end="")
        lines = [to_formatted_text(ANSI(line)) for line in stream.getvalue().splitlines()]
        line = lines[0] if lines else []
        owner._render_cache[key] = line
        return UIContent(get_line=lambda _: line, line_count=1, show_cursor=False)


class TaskProgress:
    def __init__(self, view):
        self.view = view
        self.bar = BarColumn()
        self.progress = Progress(SpinnerColumn(), TextColumn("{task.fields[label]}", markup=False,
                                     table_column=Column(no_wrap=True, overflow="ellipsis")),
                                 self.bar, TextColumn("{task.fields[percent]}", markup=False),
                                 TextColumn("{task.fields[elapsed]}", markup=False),
                                 auto_refresh=False, expand=False)
        self._timer = None
        self._loop = None
        self._render_cache = {}

    @property
    def active(self) -> bool:
        return bool(self.progress.task_ids)

    def container(self, notice=lambda: ""):
        # Separate PTK controls share one model across chat and settings layouts.
        return Window(ProgressControl(self, notice), height=1, wrap_lines=False)

    def schedule_refresh(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not loop:
            self.close()
            self._loop = loop
        if self.active and self._timer is None:
            app = get_app()
            def refresh():
                self._timer = None
                self._render_cache.clear()
                app.invalidate()
            self._timer = loop.call_later(self.view.theme.progress_refresh_interval, refresh)

    def start(self, description: str, *, total: float | None = None) -> int:
        # Treat identifiers and user titles as text, including Rich markup.
        description = " ".join(description.split())
        task = self.progress.add_task(description, total=total, label=description, percent="", elapsed="")
        self._render_cache.clear()
        self.update(task, completed=0)
        return task

    def update(self, task: int, *, completed: float, total: float | None = None) -> None:
        self.progress.update(task, completed=completed, total=total)
        current = self.progress.tasks[self.progress.task_ids.index(task)]
        current.fields["percent"] = f"{current.percentage:.0f}%" if current.total is not None else ""
        get_app().invalidate()

    def finish(self, task: int) -> None:
        self._render_cache.clear()
        if task in self.progress.task_ids:
            self.progress.remove_task(task)
        if not self.active:
            self.close()
        get_app().invalidate()

    def close(self) -> None:
        self._render_cache.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
