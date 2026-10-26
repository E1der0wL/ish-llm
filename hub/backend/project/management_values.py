"""Host management commands and view projections; Components retain write authority."""

import json
from enum import StrEnum
from hub.model import RAGAction
from llm.core.agents import AgentDefinition
from llm.components.rag.document_records import RAGDocumentInput, RAGDocument


class ManagementAction(StrEnum):
    DESCRIBE = "describe"
    LIST = "list"
    READ = "read"
    IMPORT = RAGAction.IMPORT.value
    CREATE = "create"
    SAVE = "save"
    DELETE = "delete"
    OPEN = "open"
    TOGGLE = "toggle"


class ManagementOperation:
    EMPTY_ARGUMENT = ""
    ABSENT = None
    INCLUDE_DELETED = True
    ITERATE = True
    UNSUPPORTED_MANAGER = "Unsupported component manager"
    UNSUPPORTED_DATA = "Unsupported component data contract"
    INVALID_IMPORT = "Component import must contain a JSON object"
    INVALID_DATA = "Component data must be a JSON object"
    RELOAD_SAVE = "Reload the item before saving"
    RELOAD_DELETE = "Reload the item before deleting"
    UNKNOWN = "Unknown component management operation"


class EditorRecord:
    TEXT = "text"
    VERSION = "version"
    INDENT = 2
    ENSURE_ASCII = False
    NAME = "name"

    def __init__(self, value):
        self._value = value

    @property
    def text(self):
        return self._value[self.TEXT]

    @property
    def version(self):
        return self._value[self.VERSION]

    @property
    def name(self):
        return self._value[self.NAME]

    @classmethod
    def create(cls, text, version):
        return {cls.TEXT: text, cls.VERSION: version}

    @classmethod
    def change(cls, name, text):
        return {cls.NAME: name, cls.TEXT: text}

    @classmethod
    def render(cls, values):
        return json.dumps(values, ensure_ascii=cls.ENSURE_ASCII, indent=cls.INDENT)


class ManagementEntry:
    NAME = EditorRecord.NAME
    LABEL = "label"
    DESCRIPTION = "description"
    MODIFIED = "modified"
    VERSION = EditorRecord.VERSION
    ENABLED = "enabled"
    BUILTIN = "builtin"
    EMPTY = ""
    BUILTIN_PREFIX = "builtin:"
    BUILTIN_ENTRY = True

    def __init__(self, value):
        self._value = value

    @property
    def name(self):
        return self._value[self.NAME]

    @property
    def label(self):
        return self._value.get(self.LABEL, self.name)

    @property
    def version(self):
        return self._value[self.VERSION]

    @property
    def modified(self):
        return self._value.get(self.MODIFIED)

    @property
    def description(self):
        return self._value.get(self.DESCRIPTION)

    @property
    def builtin(self):
        return self._value.get(self.BUILTIN)

    @property
    def has_enabled(self):
        return self.ENABLED in self._value

    @property
    def enabled(self):
        return self._value[self.ENABLED]

    @classmethod
    def record(cls, name, label, description, modified, version):
        return {
            cls.NAME: name,
            cls.LABEL: label,
            cls.DESCRIPTION: description,
            cls.MODIFIED: modified,
            cls.VERSION: version,
        }

    @classmethod
    def tool(cls, name, description, modified, enabled, version):
        return {
            cls.NAME: name,
            cls.DESCRIPTION: description,
            cls.MODIFIED: modified,
            cls.ENABLED: enabled,
            cls.VERSION: version,
        }

    @classmethod
    def builtin_tool(cls, name, description, enabled, version):
        return {
            cls.NAME: cls.BUILTIN_PREFIX + name,
            cls.LABEL: name,
            cls.BUILTIN: cls.BUILTIN_ENTRY,
            cls.DESCRIPTION: description,
            cls.MODIFIED: cls.EMPTY,
            cls.ENABLED: enabled,
            cls.VERSION: version,
        }


class ImportProvenance:
    SOURCE = "source"

    @classmethod
    def create(cls, path):
        return {cls.SOURCE: str(path)}

    @classmethod
    def stamp_document(cls, values, path):
        RAGDocumentInput(values).metadata[cls.SOURCE] = str(path)


class DefinitionSummary:
    """Optional presentation fields of current public snapshots, not a stored Reader."""

    TITLE = RAGDocument.Field.TITLE
    DESCRIPTION = AgentDefinition.Field.DESCRIPTION.value
    PURPOSE = AgentDefinition.Field.PURPOSE.value
    METADATA = AgentDefinition.Field.METADATA.value
    CONTENT = RAGDocumentInput.Field.CONTENT
    INSTRUCTIONS = "instructions"
    UPDATED_AT = "updated_at"
    MAXIMUM_DESCRIPTION = 200
    EMPTY = ManagementEntry.EMPTY

    @classmethod
    def entry(cls, identifier, values, version):
        metadata = values.get(cls.METADATA) or {}
        return ManagementEntry.record(
            identifier,
            values.get(cls.TITLE) or metadata.get(cls.TITLE) or identifier,
            str(
                values.get(cls.DESCRIPTION)
                or values.get(cls.PURPOSE)
                or metadata.get(cls.DESCRIPTION)
                or values.get(cls.INSTRUCTIONS)
                or values.get(cls.CONTENT)
                or cls.EMPTY
            )[: cls.MAXIMUM_DESCRIPTION],
            metadata.get(cls.UPDATED_AT, cls.EMPTY),
            version,
        )

    @classmethod
    def metadata_title(cls, metadata):
        return metadata.get(cls.TITLE)


class SelectedFileImport:
    IDENTIFIER_PATTERN = r"[^A-Za-z0-9_-]"
    REPLACEMENT = "_"
    MAXIMUM_IDENTIFIER = 64
    MODE = "r"
    ENCODING = "utf-8-sig"
    MAXIMUM_TEXT = 8 * 1024 * 1024
    OVERFLOW_PROBE = 1
    TOO_LARGE = "Import file exceeds 8 MiB of text"
