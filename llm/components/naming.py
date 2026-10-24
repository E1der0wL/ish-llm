"""Component directory and record identifier declarations, independent of storage."""


class ComponentName:
    PATTERN = r"[A-Za-z0-9_-]{1,64}"
    SCHEMA_PATTERN = "^" + PATTERN + "$"
    INVALID = "Invalid component directory or record ID"
