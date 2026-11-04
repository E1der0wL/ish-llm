"""Own the set of currently implemented dock widget settings."""
from dataclasses import dataclass
from ..core.records import Nested, Record, setting
from ..features.audio.config import VisualizerConfig
from ..features.clock.config import DateConfig, TimeConfig, WeekdayConfig
from ..features.media.config import TitleConfig, ProgressConfig, TransportConfig
from ..features.shortcuts.config import ShortcutsConfig
from ..features.weather.config import WeatherConfig

@dataclass(frozen=True, slots=True)
class WidgetsConfig(Record):
    weather: WeatherConfig = setting(
        Nested(WeatherConfig),
        factory=WeatherConfig,
        description="Maximized daily forecast. Requests send configured coordinates to Open-Meteo."
    )
    weekday_art: WeekdayConfig = setting(
        Nested(WeekdayConfig),
        factory=WeekdayConfig,
        description="Maximized pyfiglet weekday art."
    )
    date: DateConfig = setting(Nested(DateConfig), factory=DateConfig, description="Maximized date row.")
    time: TimeConfig = setting(Nested(TimeConfig), factory=TimeConfig, description="Maximized time row.")
    visualizer: VisualizerConfig = setting(
        Nested(VisualizerConfig),
        factory=VisualizerConfig,
        description="Linux output audio visualizer."
    )
    progress: ProgressConfig = setting(
        Nested(ProgressConfig),
        factory=ProgressConfig,
        description="Playback progress bar. No external player is connected."
    )
    title: TitleConfig = setting(
        Nested(TitleConfig),
        factory=TitleConfig,
        description="Application title and audio connection messages."
    )
    media_controls: TransportConfig = setting(
        Nested(TransportConfig),
        factory=TransportConfig,
        description="Display-only music buttons; no playback control is connected."
    )
    shortcuts: ShortcutsConfig = setting(
        Nested(ShortcutsConfig),
        factory=ShortcutsConfig,
        description="Shortcut entries, visibility and spacing. Disabling releases their keys."
    )

    @property
    def needs_clock(self) -> bool:
        return self.weather.needs_clock or self.weekday_art.enabled or self.date.needs_clock or self.time.needs_clock

    @property
    def needs_runtime(self) -> bool:
        return self.needs_clock or self.weather.can_fetch
