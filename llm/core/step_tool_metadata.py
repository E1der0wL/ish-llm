"""Stored Tool Step identity shared by its execution Writer and evidence readers."""


class ToolStepMetadata:
    CALL_ID = "tool_call_id"

    @classmethod
    def call_id(cls, metadata):
        return metadata.get(cls.CALL_ID)
