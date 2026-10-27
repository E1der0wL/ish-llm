"""Rich progress rendered by PTK, without a second terminal renderer."""

from hub.locales.keys import MessageKey

import asyncio
from io import StringIO

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.layout import UIContent, UIControl, Window
from prompt_toolkit.utils import get_cwidth
from dataclasses import dataclass
from weakref import WeakSet
from ...ui.output.rich_terminal import RichTerminal
from ...config.theme_style import ThemeSelector
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Column


@dataclass(slots=True)
class ProgressTaskFields:
    TEMPLATE = "{{task.fields[{field}]}}"

    label: str
    percent: str

    def to_kwargs(self):
        cls = type(self)
        return {cls.label.__name__: self.label, cls.percent.__name__: self.percent}

    @classmethod
    def label_column(cls):
        return cls.TEMPLATE.format(field=cls.label.__name__)

    @classmethod
    def percent_column(cls):
        return cls.TEMPLATE.format(field=cls.percent.__name__)

    @classmethod
    def set_label(cls, task, value):
        task.fields[cls.label.__name__] = value

    @classmethod
    def set_percent(cls, task, value):
        task.fields[cls.percent.__name__] = value


class ProgressControl(UIControl):
    EMPTY = ""
    SPACE = " "
    ROWS = 1
    SHOW_CURSOR = False
    MIN_COLUMNS = 1
    SINGLE_TASK = 1

    def __init__(self, owner, notice, annotation=lambda: ProgressControl.EMPTY, surface=None):
        self.owner = owner
        self.notice = notice
        self.annotation = annotation
        self.surface = surface

    def create_content(self, width: int, height: int) -> UIContent:
        owner = self.owner
        if not owner.visible_on(self.surface):
            return UIContent(get_line=lambda _: [], line_count=self.ROWS,
                             show_cursor=self.SHOW_CURSOR)
        tasks = owner.progress.tasks
        if not tasks:
            text = self.SPACE.join(self.notice().split())
            return UIContent(
                get_line=lambda _: [(ThemeSelector.NOTICE.style, text)] if text else [],
                line_count=self.ROWS,
                show_cursor=self.SHOW_CURSOR,
            )
        owner.schedule_refresh()
        theme = owner.view.theme
        annotation = self.annotation()
        key = (width, theme, annotation)
        if key in owner._render_cache:
            line = owner._render_cache[key]
            return UIContent(
                get_line=lambda _: line,
                line_count=self.ROWS,
                show_cursor=self.SHOW_CURSOR,
            )
        owner.bar.style = theme.comment2
        owner.bar.complete_style = owner.bar.finished_style = owner.bar.pulse_style = (
            theme.accent
        )
        task = tasks[-1]
        ProgressTaskFields.set_label(
            task,
            task.description
            + (
                owner.view.t(MessageKey.PROGRESS_MORE, count=len(tasks) - 1)
                if len(tasks) > self.SINGLE_TASK
                else self.EMPTY
            ),
        )
        stream = StringIO()
        console = RichTerminal.console(
            file=stream,
            width=max(
                self.MIN_COLUMNS, width - get_cwidth(annotation) - bool(annotation)
            ),
        )
        console.print(owner.progress.make_tasks_table([task]), end=self.EMPTY)
        lines = [
            to_formatted_text(ANSI(line)) for line in stream.getvalue().splitlines()
        ]
        line = lines[0] if lines else []
        if annotation:
            line = [*line, (ThemeSelector.NOTICE.style, self.SPACE + annotation)]
        owner._render_cache[key] = line
        return UIContent(
            get_line=lambda _: line, line_count=self.ROWS, show_cursor=self.SHOW_CURSOR
        )


class TaskProgress:
    ABSENT = None
    EMPTY = ""
    SPACE = " "
    MARKUP = False
    NO_WRAP = True
    OVERFLOW = "ellipsis"
    AUTO_REFRESH = False
    EXPAND = False
    ROWS = 1
    WRAP_LINES = False
    INITIAL_COMPLETED = 0
    INDETERMINATE = False
    PERCENT_FORMAT = "{percentage:.0f}%"

    def __init__(self, view):
        self.view = view
        self.bar = BarColumn()
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn(
                ProgressTaskFields.label_column(),
                markup=self.MARKUP,
                table_column=Column(no_wrap=self.NO_WRAP, overflow=self.OVERFLOW),
            ),
            self.bar,
            TextColumn(ProgressTaskFields.percent_column(), markup=self.MARKUP),
            auto_refresh=self.AUTO_REFRESH,
            expand=self.EXPAND,
        )
        self._timer = self.ABSENT
        self._loop = self.ABSENT
        self._render_cache = {}
        self._surfaces = WeakSet()

    def visible_on(self, surface):
        current = self.view._dialog
        return current is surface if surface is not self.ABSENT else current not in self._surfaces

    @property
    def active(self) -> bool:
        return bool(self.progress.task_ids)

    def container(
        self,
        notice=lambda: TaskProgress.EMPTY,
        *,
        annotation=lambda: ProgressControl.EMPTY,
        surface=ABSENT,
    ):
        # Separate PTK controls share one model across chat and settings layouts.
        if surface is not self.ABSENT:
            self._surfaces.add(surface)
        return Window(
            ProgressControl(self, notice, annotation, surface),
            height=self.ROWS,
            wrap_lines=self.WRAP_LINES,
        )

    def schedule_refresh(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not loop:
            self.close()
            self._loop = loop
        if self.active and self._timer is self.ABSENT:
            app = get_app()

            def refresh():
                self._timer = self.ABSENT
                self._render_cache.clear()
                app.invalidate()

            self._timer = loop.call_later(
                self.view.theme.progress_refresh_interval, refresh
            )

    def start(self, description: str, *, total: float | None = ABSENT) -> int:
        # Treat identifiers and user titles as text, including Rich markup.
        description = self.SPACE.join(description.split())
        task = self.progress.add_task(
            description,
            total=total,
            **ProgressTaskFields(description, self.EMPTY).to_kwargs()
        )
        self._render_cache.clear()
        self.update(task, completed=self.INITIAL_COMPLETED)
        get_app().invalidate()
        return task

    def update(
        self,
        task: int,
        *,
        completed: float = INITIAL_COMPLETED,
        total: float | None = ABSENT,
        description: str | None = ABSENT,
        indeterminate: bool = INDETERMINATE
    ) -> None:
        if task not in self.progress.task_ids:
            return
        self._render_cache.clear()
        self.progress.update(task, completed=completed, total=total)
        current = self.progress.tasks[self.progress.task_ids.index(task)]
        if description is not self.ABSENT:
            current.description = self.SPACE.join(description.split())
        if indeterminate:
            current.total = self.ABSENT
            current.finished_time = self.ABSENT
            current.finished_speed = self.ABSENT
        ProgressTaskFields.set_percent(
            current,
            (
                self.PERCENT_FORMAT.format(percentage=current.percentage)
                if current.total is not self.ABSENT
                else self.EMPTY
            ),
        )
        self.schedule_refresh()

    def observe(self, task, update):
        self.update(task, description=self.view.t(update.key), completed=update.completed,
                    total=update.total, indeterminate=update.total is None)

    def callback(self, task, completed):
        return ProgressCompletion(self, task, completed)

    def finish(self, task: int) -> None:
        self._render_cache.clear()
        if task in self.progress.task_ids:
            self.progress.remove_task(task)
        if not self.active:
            self.close()
        get_app().invalidate()

    def close(self) -> None:
        self._render_cache.clear()
        if self._timer is not self.ABSENT:
            self._timer.cancel()
            self._timer = self.ABSENT


@dataclass(slots=True)
class ProgressCompletion:
    """Keep a request's stage receiver attached to its own completion callback."""
    owner: TaskProgress
    task: int
    completed: object

    def __call__(self, *args, **kwargs):
        return self.completed(*args, **kwargs)

    def stage(self, update):
        self.owner.observe(self.task, update)
