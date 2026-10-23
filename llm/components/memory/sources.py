"""Memory provenance projections; callers retain admission and storage authority."""


class MemorySource:
    API = "api"
    TOOL = "tool"
    PROCESSING = "processing"
    CONSOLIDATION = "consolidation"

    class Field:
        KIND = "kind"
        PROJECT_ID = "project_id"
        SESSION_ID = "session_id"
        RUN_ID = "run_id"
        MESSAGE_ID = "message_id"
        STEP_ID = "step_id"
        MESSAGE_IDS = "message_ids"
        INPUT_MESSAGE_ID = "input_message_id"
        OPERATION_ID = "operation_id"

    def __init__(self, value):
        self._value = value

    @classmethod
    def api_input(cls, source):
        return source or {cls.Field.KIND: cls.API}

    @classmethod
    def ensure_project(cls, value, identifier):
        value.setdefault(cls.Field.PROJECT_ID, identifier)
        return value

    @classmethod
    def tool(cls, call):
        return {cls.Field.KIND: cls.TOOL, cls.Field.PROJECT_ID: call.project_id,
                cls.Field.SESSION_ID: call.session_id, cls.Field.RUN_ID: call.run_id,
                cls.Field.MESSAGE_ID: call.input_message_id, cls.Field.STEP_ID: call.step_id}

    @classmethod
    def processing(cls, context):
        return {cls.Field.KIND: cls.PROCESSING, cls.Field.PROJECT_ID: context.project.id,
                cls.Field.SESSION_ID: context.session.id, cls.Field.RUN_ID: context.run.id,
                cls.Field.MESSAGE_ID: context.run.input_message_id}

    @classmethod
    def collect_history_references(cls, value, runs, messages):
        if isinstance(value, dict):
            if value.get(cls.Field.RUN_ID):
                runs.add(value[cls.Field.RUN_ID])
            messages.update(value.get(cls.Field.MESSAGE_IDS, []))
            if value.get(cls.Field.INPUT_MESSAGE_ID):
                messages.add(value[cls.Field.INPUT_MESSAGE_ID])

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @classmethod
    def consolidation(cls, source, operation_id, *, kind):
        return {**(source or {}), cls.Field.KIND: kind, cls.Field.OPERATION_ID: operation_id}

    @property
    def kind(self):
        return self._value.get(self.Field.KIND)

    @property
    def operation_id(self):
        return self._value.get(self.Field.OPERATION_ID)

    @property
    def project_id(self):
        return self._value.get(self.Field.PROJECT_ID)
