"""JSON with line/block comments and owner-provided setting documentation."""

import json

from ..core.records import Record


class Jsonc:
    SUFFIX = ".jsonc"
    INDENT = 2
    HEADER = ("dok configuration: edit values, then run ish_reload to apply them.",
              "Line comments (//) and block comments (/* ... */) are supported. Trailing commas are not allowed.",
              "All settings are shown. Explicit values take precedence; omitted fields use this version's defaults.",
              "Widths and horizontal gaps use terminal cells; spacing.after/basic_after use rows. _s means seconds; _min means minutes.",
              "Edit shared RGB colors in theme.colors and map UI roles in theme.roles. null roles follow the terminal.",
              "Other color/background fields use theme role or palette names; region nulls inherit as documented.",
              "toolbar.background also accepts a toolbar-only #RRGGBB override; toolbar outer padding is measured in cells.",
              "Shortcut entry colors and visualizer gradient stops remain independent #RRGGBB values.")

    @staticmethod
    def uncomment(text: str) -> str:
        """Replace comments with whitespace, preserving strings and error positions."""
        result = list(text)
        index, quoted = 0, False
        while index < len(text):
            char = text[index]
            if quoted:
                if char == "\\":
                    index += 2
                    continue
                if char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif text.startswith("//", index) or text.startswith("/*", index):
                start = index
                if text[index + 1] == "/":
                    index += 2
                    while index < len(text) and text[index] not in "\r\n":
                        index += 1
                else:
                    end = text.find("*/", index + 2)
                    if end == -1:
                        raise json.JSONDecodeError("Unterminated block comment", text, start)
                    index = end + 2
                for position in range(start, index):
                    if text[position] not in "\r\n":
                        result[position] = " "
                continue
            index += 1
        return "".join(result)

    @classmethod
    def loads(cls, text: str, **kwargs):
        return json.loads(cls.uncomment(text), **kwargs)

    @classmethod
    def _lines(cls, value, level: int) -> list[str]:
        prefix = " " * (level * cls.INDENT)
        child_prefix = prefix + " " * cls.INDENT
        if isinstance(value, Record):
            fields = value.documented_fields()
            result = ["{"]
            for index, item in enumerate(fields):
                result.extend(child_prefix + "// " + line for line in item.description.splitlines())
                lines = cls._lines(item.value, level + 1)
                lines[0] = child_prefix + json.dumps(item.name, ensure_ascii=False) + ": " + lines[0]
                lines[-1] += "," if index + 1 < len(fields) else ""
                result.extend(lines)
            result.append(prefix + "}")
            return result
        if isinstance(value, (list, tuple)) and any(isinstance(item, Record) for item in value):
            result = ["["]
            for index, item in enumerate(value):
                lines = cls._lines(item, level + 1)
                lines[0] = child_prefix + lines[0]
                lines[-1] += "," if index + 1 < len(value) else ""
                result.extend(lines)
            result.append(prefix + "]")
            return result
        return [json.dumps(value, ensure_ascii=False, allow_nan=False)]

    @classmethod
    def dumps(cls, record: Record) -> str:
        return "\n".join([*("// " + line for line in cls.HEADER), *cls._lines(record, 0), ""])
