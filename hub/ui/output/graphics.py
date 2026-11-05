"""Kitty image placements owned by one Hub window, never model-supplied escapes."""

import base64
import os
import secrets


class KittyGraphics:
    # Kitty's transport maximum, a protocol constant rather than a resource limit.
    CHUNK_BYTES = 4096
    MULTIPLEXERS = ('TMUX', 'STY')
    WINDOW_ID = 'KITTY_WINDOW_ID'
    TERMINAL = 'TERM'
    TERMINAL_NAME = 'xterm-kitty'
    ENCODING = 'ascii'
    UPLOAD = 'a=t,f=100,I={number},q=2,'
    CHUNK = '\x1b_G{header}m={more};{chunk}\x1b\\'
    HIDE = '\x1b_Ga=d,d=n,I={number},q=2\x1b\\'
    DELETE = '\x1b_Ga=d,d=N,I={number},q=2\x1b\\'
    PLACE = '\x1b_Ga=p,I={number},p=1,c={width},r={height},C=1,q=2\x1b\\'
    SAVE_CURSOR = '\x1b7'
    RESTORE_CURSOR = '\x1b8'
    LAST_SCREEN = '_last_screen'
    MAXIMUM_NUMBER = (1 << 32) - 1

    @staticmethod
    def supported(environment=None):
        environment = os.environ if environment is None else environment
        if any(environment.get(key) for key in KittyGraphics.MULTIPLEXERS):
            return False  # Multiplexer passthrough needs a separate confirmed adapter.
        return bool(environment.get(KittyGraphics.WINDOW_ID)) or environment.get(KittyGraphics.TERMINAL) == KittyGraphics.TERMINAL_NAME

    @classmethod
    def encode(cls, data, number):
        payload = base64.b64encode(data).decode(cls.ENCODING)
        commands = []
        for offset in range(0, len(payload), cls.CHUNK_BYTES):
            chunk = payload[offset:offset + cls.CHUNK_BYTES]
            more = int(offset + len(chunk) < len(payload))
            header = cls.UPLOAD.format(number=number) if offset == 0 else ''
            commands.append(cls.CHUNK.format(header=header, more=more, chunk=chunk))
        return ''.join(commands)

    def __init__(self, app, window, payload, number):
        self.app, self.window, self.payload, self.number = app, window, payload, number
        self.uploaded, self.closed = False, False
        app.before_render += self._before
        app.after_render += self._after

    @staticmethod
    def create_number():
        # Image *numbers* request a new terminal-owned ID instead of replacing
        # somebody else's image ID. Deletion always addresses this number only.
        return secrets.randbelow(KittyGraphics.MAXIMUM_NUMBER) + 1

    def _before(self, app):
        if self.uploaded and not self.closed:
            app.output.write_raw(self.HIDE.format(number=self.number))

    def _after(self, app):
        if self.closed or app.is_done:
            return
        # PTK exposes the last painted window positions only on its renderer.
        # Keep this narrow terminal integration isolated from the transcript and
        # Backend; absence of a visible placement means no graphics are emitted.
        screen = getattr(app.renderer, self.LAST_SCREEN, None)
        if screen is None:
            return
        position = screen.visible_windows_to_write_positions.get(self.window)
        if position is None:
            if self.uploaded:
                app.output.write_raw(self.DELETE.format(number=self.number))
                app.output.flush()
                self.uploaded = False
            return
        size = app.output.get_size()
        x, y = max(0, position.xpos), max(0, position.ypos)
        width = min(position.width, size.columns - x)
        height = min(position.height, size.rows - y)
        if width <= 0 or height <= 0:
            return
        output = app.output
        output.write_raw(self.SAVE_CURSOR)
        try:
            output.cursor_goto(y, x)
            if not self.uploaded:
                output.write_raw(self.payload)
                self.uploaded = True
            output.write_raw(self.PLACE.format(number=self.number, width=width, height=height))
        finally:
            output.write_raw(self.RESTORE_CURSOR)
            output.flush()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.app.before_render -= self._before
        self.app.after_render -= self._after
        if self.uploaded:
            self.app.output.write_raw(self.DELETE.format(number=self.number))
            self.app.output.flush()
