"""Title, progress and display-only transport widgets."""

from ...rendering.presentation import DockStyle, RenderContext, swallow_mouse
from ...rendering.text import fit, fit_labels, width
from .config import ProgressConfig, TitleConfig, TransportConfig


class ProgressView:
    TIME_SEPARATOR = " "

    def __init__(self, config: ProgressConfig):
        self.element = config

    @staticmethod
    def _time(seconds):
        if seconds is None:
            return "--:--"
        minutes, seconds = divmod(int(seconds), 60)
        return f"{minutes:02}:{seconds:02}"

    def render(self, context: RenderContext) -> tuple:
        snapshot = context.playback
        left, right = self._time(snapshot.position), self._time(snapshot.duration)
        length = self.element.display_length(context.width - width(left + right) - 2 * width(self.TIME_SEPARATOR))
        filled = int(snapshot.progress * length)
        text = self.TIME_SEPARATOR.join((left, "━" * filled + "─" * (length - filled), right))
        return ((DockStyle.foreground(self.element.style.color), fit(text, context.width), swallow_mouse),)


class TitleView:
    def __init__(self, config: TitleConfig):
        self.element = config

    def render(self, context: RenderContext) -> tuple:
        snapshot, audio = context.playback, context.audio
        title = snapshot.caption if snapshot.has_metadata else self.element.application_caption(audio.applications) or snapshot.caption
        diagnostic = context.diagnostic or self.element.audio_error_caption(audio.diagnostic)
        style = DockStyle.ERROR if diagnostic else DockStyle.foreground(self.element.style.color)
        return ((style, fit(diagnostic or title, context.width), swallow_mouse),)


class TransportView:
    def __init__(self, config: TransportConfig):
        self.element = config

    def render(self, context: RenderContext) -> tuple:
        buttons = self.element.buttons
        separator = self.element.SEPARATOR
        labels = fit_labels([button.display() for button in buttons], context.width, separator)
        result = []
        for index, (button, label) in enumerate(zip(buttons, labels)):
            if index:
                result.append((DockStyle.BACKGROUND, separator, swallow_mouse))
            result.append((DockStyle.foreground(button.style.color), label, swallow_mouse))
        return tuple(result)
