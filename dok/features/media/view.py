"""Title, progress and capability-aware transport widgets."""

from ...rendering.presentation import RenderContext, swallow_mouse
from ...rendering.text import fit, fit_labels, width
from .config import ProgressConfig, TitleConfig, TransportConfig
from ...core.theme import ThemeRole


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
        track_style = context.theme.paint(self.element.style.color)
        if not length:
            labels = self.TIME_SEPARATOR.join((left, "", right))
            return ((track_style, fit(labels, context.width), swallow_mouse),)

        last = length - 1
        handle = round(snapshot.progress * last)
        played_style = context.theme.paint()
        played = "━" * handle + "●"
        remaining = "─" * (last - handle)
        result = [(track_style, left + self.TIME_SEPARATOR, swallow_mouse)]
        if last and self._interaction is not None and self._interaction.can_seek:
            for index, char in enumerate(played + remaining):
                style = played_style if index <= handle else track_style
                result.append((style, char, self._interaction.seek(index / last)))
        else:
            result.append((played_style, played, swallow_mouse))
            if remaining:
                result.append((track_style, remaining, swallow_mouse))
        result.append((track_style, self.TIME_SEPARATOR + right, swallow_mouse))
        return tuple(result)


class TitleView:
    def __init__(self, config: TitleConfig):
        self.element = config

    def render(self, context: RenderContext) -> tuple:
        snapshot, audio = context.playback, context.audio
        title = snapshot.display_caption(context.locale) if snapshot.has_metadata else self.element.application_caption(audio.applications, context.locale) or snapshot.display_caption(context.locale)
        diagnostic = context.diagnostic or self.element.audio_error_caption(audio.diagnostic, context.locale)
        style = context.theme.paint(ThemeRole.WARNING if diagnostic else self.element.style.color)
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
                result.append((context.theme.paint(), separator, swallow_mouse))
            enabled = self._interaction is not None and self._interaction.supports(button.action)
            style = context.theme.paint(button.style.color)
            if self._interaction is not None and not (enabled or button.key and self._interaction.available(button.action)):
                style += " dim"
            result.append((style, label, self._interaction.button(button.action) if enabled else swallow_mouse))
        return tuple(result)
