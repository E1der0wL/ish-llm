"""Stage-based completion progress, localized and independent of the shell UI."""
from dataclasses import dataclass

from ...core.theme import Theme, ThemeRole
from ...locales import Message as M
from ...popups.models import PopupContent
from ...rendering.presentation import TextRow
from ...rendering.progress import progress_bar
from .runtime import Phase


@dataclass(frozen=True, slots=True)
class CompletionNotice(PopupContent):
    compact: bool = True
    completed: int | None = None

    def rows_at(self, columns: int, theme: Theme) -> tuple[TextRow, ...]:
        if self.completed is None:
            return self.rows
        return (*self.rows, CompletionProgress.bar(self.completed, columns, theme))


class CompletionProgress:
    KEY = "dok.completion"
    STAGES = (Phase.EXECUTING, Phase.ANALYZING, Phase.SAVING)

    @classmethod
    def bar(cls, stage, columns, theme=Theme()):
        """The Rich bar counts completed stages, never estimated model progress."""
        return progress_bar(stage, len(cls.STAGES), columns, theme)

    @classmethod
    def content(cls, progress, locale, prefix):
        stage = cls.STAGES.index(progress.phase)
        label = {Phase.EXECUTING: M.COMPLETION_EXECUTING,
                 Phase.ANALYZING: M.COMPLETION_ANALYZING_HUB if progress.hub else M.COMPLETION_ANALYZING_LOCAL,
                 Phase.SAVING: M.COMPLETION_SAVING}[progress.phase]
        return CompletionNotice(locale.text(M.COMPLETIONS), (
            TextRow.plain(prefix, ThemeRole.FOREGROUND),
            TextRow.plain(locale.text(M.COMPLETION_STAGE, label=locale.text(label),
                                     step=stage + 1, total=len(cls.STAGES)), ThemeRole.MUTED)),
            completed=stage)

    @classmethod
    def finished(cls, result, locale, prefix):
        if result.error:
            return CompletionNotice(locale.text(M.COMPLETIONS), tuple(
                TextRow.plain(line, ThemeRole.ERROR)
                for line in locale.text(M.ERROR_HINT, message=result.error).splitlines()))
        return CompletionNotice(locale.text(M.COMPLETIONS), (
            TextRow.plain(prefix, ThemeRole.FOREGROUND),
            TextRow.plain(locale.text(M.COMPLETION_DONE, count=result.count), ThemeRole.SAVE)),
            completed=len(cls.STAGES))
