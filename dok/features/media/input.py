"""Pointer gestures belong to media; seeking commits once on release."""
from prompt_toolkit.mouse_events import MouseButton, MouseEventType


class MediaInput:
    def __init__(self, runtime, start_task, changed, enabled=lambda: True):
        self._runtime, self._start, self._changed = runtime, start_task, changed
        self._enabled = enabled
        self._drag = None

    @property
    def dragging(self):
        return self._drag is not None

    def cancel(self):
        if self._drag is not None:
            self._drag = None
            self._changed()

    def preview(self, playback):
        from dataclasses import replace
        if self._drag is not None:
            player, fraction = self._drag
            if player.token == self._runtime.state.token:
                return replace(playback, position=player.seek_position(fraction))
            self.cancel()
        return playback

    def supports(self, action):
        return self._enabled() and not self._runtime.busy and self._runtime.state.supports(action)

    @property
    def can_seek(self):
        return self._enabled() and not self._runtime.busy and self._runtime.state.can_seek

    def button(self, action):
        expected = self._runtime.state
        def handle(event):
            if (self._enabled() and not self.dragging and event.button == MouseButton.LEFT
                    and event.event_type == MouseEventType.MOUSE_UP and not event.modifiers):
                self._runtime.request(expected, action=action, start_task=self._start)
            return None
        return handle

    def seek(self, fraction):
        expected = self._runtime.state
        def handle(event):
            if not self._enabled() or event.button != MouseButton.LEFT or event.modifiers:
                return None
            if event.event_type == MouseEventType.MOUSE_DOWN and self.can_seek:
                self._drag = (expected, fraction)
                self._changed()
            elif self._drag is not None:
                original, _ = self._drag
                if original.token != expected.token:
                    self.cancel()
                elif event.event_type == MouseEventType.MOUSE_MOVE:
                    self._drag = (original, fraction)
                    self._changed()
                elif event.event_type == MouseEventType.MOUSE_UP:
                    self.cancel()
                    self._runtime.request(original, fraction=fraction, start_task=self._start)
            return None
        return handle
