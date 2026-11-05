"""Title, progress and capability-aware transport widgets."""

from ...rendering.presentation import DockStyle, RenderContext, swallow_mouse
from ...rendering.text import fit, fit_labels, width
from .config import ProgressConfig, TitleConfig, TransportConfig


class ProgressView:
    TIME_SEPARATOR = " "

    def __init__(self, config: ProgressConfig, interaction=None):
        self.element = config
        self._interaction = interaction

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
        if self._interaction is not None and self._interaction.can_seek and length > 1:
            style = DockStyle.foreground(self.element.style.color)
            return ((style, left + self.TIME_SEPARATOR, swallow_mouse), *(
                (style, "━" if index < filled else "─", self._interaction.seek(index / (length - 1)))
                for index in range(length)
            ), (style, self.TIME_SEPARATOR + right, swallow_mouse))
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
    def __init__(self, config: TransportConfig, interaction=None):
        self.element = config
        self._interaction = interaction

    def render(self, context: RenderContext) -> tuple:
        buttons = self.element.buttons
        separator = self.element.SEPARATOR
        labels = fit_labels([button.display() for button in buttons], context.width, separator)
        result = []
        for index, (button, label) in enumerate(zip(buttons, labels)):
            if index:
                result.append((DockStyle.BACKGROUND, separator, swallow_mouse))
            enabled = self._interaction is not None and self._interaction.supports(button.action)
            style = DockStyle.foreground(button.style.color)
            if self._interaction is not None and not enabled:
                style += " dim"
            result.append((style, label, self._interaction.button(button.action) if enabled else swallow_mouse))
        return tuple(result)
