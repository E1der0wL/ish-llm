"""Interactive preview using the real ish layout; submitted commands are printed, not executed."""
import asyncio
import json
import os
from contextlib import closing
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prompt_toolkit.input import create_input
from prompt_toolkit.output import create_output
from dok import AudioConfig, Shortcut, WeatherConfig, Width, install
from dok import DockConfig, DockSettings, WidgetsConfig, ShortcutsConfig
from tests.dok.support import HostPrompt

async def preview(*, audio_enabled=True, offline=False):
    print('dok preview: Ctrl+W hide/show (automatic size); Alt+1 git; Alt+2 rg -n; Alt+3 ls -la; Ctrl+D exit')
    print('Submitted commands are printed only. No shell commands are executed.')
    with tempfile.TemporaryDirectory() as directory, closing(create_input()) as input:
        prompt = HostPrompt(input, create_output())
        dock = install(prompt, config_path=Path(directory) / 'config.jsonc')
        # Separate observation pipe: no diagnostics are injected into terminal input/output.
        probe_fd = os.environ.get("DOK_TEST_POPUP_FD")
        previous_popup = None
        def observe_popup(app):
            nonlocal previous_popup
            state = (dock._popups.modal, dock._popups.page.content().title if dock._popups.modal else "")
            if probe_fd is not None and state != previous_popup:
                os.write(int(probe_fd), (json.dumps({"visible": state[0], "title": state[1]}) + "\n").encode())
                previous_popup = state
        prompt.app.after_render += observe_popup
        dock.configure(DockConfig(
            media={'enabled': not offline},
            audio=AudioConfig(enabled=audio_enabled and not offline),
            widgets=WidgetsConfig(
                weather=WeatherConfig(),
                shortcuts=ShortcutsConfig(items=(
                    Shortcut(1, 'git', icon='🌿', label='Git', key=('escape', '1')),
                    Shortcut(2, 'rg -n', icon='🔎', label='검색', key=('escape', '2')),
                    Shortcut(3, 'ls -la', icon='📁', label='파일', key=('escape', '3'))
                ))
            ),
            dock=DockSettings(width=Width(mode='ratio', value=0.8))
        ))
        dock.show()
        try:
            while True:
                try:
                    command = await prompt.read_and_dispatch()
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
                print(f'Submitted: {command!r}')
        finally:
            prompt.app.after_render -= observe_popup
            dock.uninstall()
if __name__ == '__main__':
    asyncio.run(preview(audio_enabled='--no-audio' not in sys.argv, offline='--offline' in sys.argv))
