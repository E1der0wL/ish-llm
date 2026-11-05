"""Interactive preview using the real ish layout; submitted commands are printed, not executed."""
import asyncio
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
            dock.uninstall()
if __name__ == '__main__':
    asyncio.run(preview(audio_enabled='--no-audio' not in sys.argv, offline='--offline' in sys.argv))
