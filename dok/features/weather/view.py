"""Forecast rows, icon alignment and temperature styling."""
from __future__ import annotations

from datetime import date

from ...rendering.presentation import TextRow, TextSpan
from ...rendering.text import fit, width
from .models import WeatherSnapshot


class WeatherPresentation:
    ROWS = 3
    CELL_WIDTH = 8

    @classmethod
    def lines(cls, config, snapshot, available, *, today: date | None = None):
        return tuple(row.text for row in cls.rows(config, snapshot, available, today=today))

    @classmethod
    def rows(cls, config, snapshot, available, *, today: date | None = None):
        if not config.enabled:
            return ()
        if config.location is None:
            snapshot = WeatherSnapshot.unknown(today if today is not None else date.today(), config.days)
        count = min(config.days, len(snapshot.days), max(1, available // cls.CELL_WIDTH))
        days = snapshot.days[:count]
        if not days:
            status = config.texts.unavailable if snapshot.error else "날씨 불러오는 중"
            return (TextRow.plain(fit(status, available), config.style.color),) + (TextRow(),) * (cls.ROWS - 1)
        # Aim for half the previous full-width row; keep temperatures separated.
        needed = max(width(item.temperatures) for item in days) + 1
        cells = min(available // count, max(needed, int(available * config.width_ratio) // count))
        def row(values, offset=0):
            parts = []
            for value in values:
                content = value.fitted(cells)
                left = max(0, min(cells - content.cells, (cells - content.cells) // 2 + offset))
                parts.append(TextSpan(" " * left, config.style.color))
                parts.extend(content.spans)
                parts.append(TextSpan(" " * (cells - content.cells - left), config.style.color))
            return TextRow(tuple(parts))
        result = (
            row(TextRow.plain(config.weekday_label(item.day), config.style.color) for item in days),
            row(
                (TextRow.plain(config.icons.for_kind(item.kind), config.style.color) for item in days),
                config.icon_offset_columns
            ),
            row(TextRow((TextSpan(item.temperature_parts[0] + " ", config.style.color),
                         TextSpan(
                             item.temperature_parts[1],
                             config.style.low_temperature_color
                         ))) for item in days),
        )
        if snapshot.error:
            status = config.texts.unavailable
            if snapshot.updated_at is not None:
                status += " · " + snapshot.updated_at.strftime("%m/%d %H:%M")
            result += (TextRow.plain(fit(status, available), config.style.low_temperature_color),)
        return result
