"""Goal definitions, scopes and reference projections; no Run or Tool authority."""

from copy import deepcopy
from enum import StrEnum

from llm.core.models import ResourceKind
from llm.core.schema import (SchemaType, SchemaKeyword, SchemaBoolean,
                             object_schema, field, metadata_schema, implementation_schema)
from llm.core.implementation_parameters import ImplementationParameters
from llm.services.infrastructure.storage import revision_token


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class GoalScope:
    class Field:
        TYPE = "type"
        ID = "id"

    def __init__(self, value):
        self._value = value

    @classmethod
    def project(cls):
        return {cls.Field.TYPE: ResourceKind.PROJECT.value}

    @classmethod
    def session(cls, identifier):
        return {cls.Field.TYPE: ResourceKind.SESSION.value, cls.Field.ID: identifier}

    @property
    def is_project(self):
        return self._value[self.Field.TYPE] == ResourceKind.PROJECT.value

    @property
    def is_session(self):
        return self._value[self.Field.TYPE] == ResourceKind.SESSION.value

    @property
    def session_id(self):
        return self._value[self.Field.ID]

    def includes(self, session_id):
        return self._value in (self.project(), self.session(session_id))


class GoalRunReference:
    class Field:
        SESSION_ID = "session_id"
        RUN_ID = "run_id"
        RELATION = "relation"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, session_id, run_id, relation):
        return {cls.Field.SESSION_ID: session_id, cls.Field.RUN_ID: run_id, cls.Field.RELATION: relation}

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @property
    def run_id(self):
        return self._value[self.Field.RUN_ID]

    def matches(self, session_id, run_id):
        return (self.session_id, self.run_id) == (session_id, run_id)


class GoalDefinition:
    COMPONENT = "goals"
    IDENTIFIER_PATTERN = "^[A-Za-z0-9_-]{1,64}$"
    MINIMUM_TEXT_LENGTH = 1

    class Field:
        TITLE = "title"
        OBJECTIVE = "objective"
        SCOPE = "scope"
        STATUS = "status"
        SUCCESS_CRITERIA = "success_criteria"
        PROGRESS = "progress"
        NEXT_ACTIONS = "next_actions"
        RUN_REFS = "run_refs"
        METADATA = "metadata"

    LIST_FIELDS = (Field.SUCCESS_CRITERIA, Field.PROGRESS, Field.NEXT_ACTIONS)
    CONTEXT_FIELDS = (Field.OBJECTIVE, *LIST_FIELDS)

    @classmethod
    def identifier_schema(cls):
        return field(SchemaType.STRING, **{SchemaKeyword.PATTERN: cls.IDENTIFIER_PATTERN})

    @classmethod
    def text_schema(cls):
        return field(SchemaType.STRING, **{SchemaKeyword.MIN_LENGTH: cls.MINIMUM_TEXT_LENGTH})

    @classmethod
    def list_schema(cls):
        return {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.ITEMS: cls.text_schema()}

    @classmethod
    def schema(cls):
        scope = GoalScope.Field
        reference = GoalRunReference.Field
        return object_schema({
            cls.Field.TITLE: cls.text_schema(), cls.Field.OBJECTIVE: cls.text_schema(),
            cls.Field.SCOPE: {SchemaKeyword.ONE_OF: [
                object_schema({scope.TYPE: {SchemaKeyword.CONST: ResourceKind.PROJECT.value}},
                    **{SchemaKeyword.REQUIRED: [scope.TYPE], SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT}),
                object_schema({scope.TYPE: {SchemaKeyword.CONST: ResourceKind.SESSION.value}, scope.ID: cls.identifier_schema()},
                    **{SchemaKeyword.REQUIRED: [scope.TYPE, scope.ID], SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT})]},
            cls.Field.STATUS: field(SchemaType.STRING, **{SchemaKeyword.ENUM: [status.value for status in GoalStatus]}),
            **{key: cls.list_schema() for key in cls.LIST_FIELDS},
            cls.Field.RUN_REFS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                SchemaKeyword.ITEMS: object_schema({reference.SESSION_ID: cls.identifier_schema(),
                    reference.RUN_ID: cls.identifier_schema(), reference.RELATION: cls.text_schema()},
                    **{SchemaKeyword.REQUIRED: [reference.SESSION_ID, reference.RUN_ID, reference.RELATION],
                       SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT})},
            cls.Field.METADATA: metadata_schema(),
        }, **{SchemaKeyword.REQUIRED: [cls.Field.TITLE, cls.Field.OBJECTIVE, cls.Field.SCOPE, cls.Field.STATUS]})


class GoalContextReference:
    GOAL_ID = "goal_id"
    VERSION = "version"
    SUMMARY_FIELDS = (GOAL_ID, VERSION, GoalDefinition.Field.OBJECTIVE, GoalDefinition.Field.SUCCESS_CRITERIA)

    @classmethod
    def project(cls, identifier, value):
        return {cls.GOAL_ID: identifier, cls.VERSION: revision_token(value),
                **{name: deepcopy(value[name]) for name in GoalDefinition.CONTEXT_FIELDS if name in value}}

    @classmethod
    def for_summary(cls, value):
        return {name: deepcopy(value[name]) for name in cls.SUMMARY_FIELDS if name in value}


class GoalCloneOrigin:
    KEY = "cloned_from"
    PROJECT_ID = "project_id"
    GOAL_ID = GoalContextReference.GOAL_ID

    @classmethod
    def project(cls, project_id, identifier):
        return {cls.PROJECT_ID: project_id, cls.GOAL_ID: identifier}


class GoalRecord:
    def __init__(self, value):
        self._value = value

    @property
    def scope(self):
        return GoalScope(self._value[GoalDefinition.Field.SCOPE])

    @property
    def status(self):
        return self._value[GoalDefinition.Field.STATUS]

    @property
    def run_refs(self):
        return self._value.get(GoalDefinition.Field.RUN_REFS, [])

    def validate_references(self, reference):
        if self.scope.is_session:
            reference(self.scope.session_id)
        for raw in self.run_refs:
            selected = GoalRunReference(raw)
            reference(selected.session_id, selected.run_id)

    @classmethod
    def references_change(cls, refs):
        return {GoalDefinition.Field.RUN_REFS: refs}

    @classmethod
    def status_change(cls, status):
        return {GoalDefinition.Field.STATUS: status}

    def cloned(self, project_id, identifier):
        value = deepcopy(self._value)
        value[GoalDefinition.Field.RUN_REFS] = []
        value.setdefault(GoalDefinition.Field.METADATA, {})[GoalCloneOrigin.KEY] = GoalCloneOrigin.project(project_id, identifier)
        return value


class GoalSettings:
    PRIORITY = "priority"
    IDENTIFIERS = "identifiers"
    NESTED_AGENT_IDS = "nested_agent_ids"
    INJECT = "inject"
    WRITE_TOOLS = "write_tools"
    INACTIVE_PRIORITY = 0
    SELECTED = True
    MISSING_PRIORITY = "Goal context injection requires config.priority"

    def __init__(self, value):
        self._parameters = ImplementationParameters.read(value)

    @classmethod
    def schema(cls):
        return implementation_schema(config=object_schema({
            cls.PRIORITY: field(SchemaType.INTEGER),
            cls.IDENTIFIERS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                             SchemaKeyword.ITEMS: GoalDefinition.identifier_schema()},
            cls.NESTED_AGENT_IDS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.ITEMS: GoalDefinition.text_schema()},
        }, **{SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT}),
            policy=object_schema({cls.INJECT: field(SchemaType.BOOLEAN), cls.WRITE_TOOLS: field(SchemaType.BOOLEAN)},
                                 **{SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT}))

    @property
    def inject(self):
        return self._parameters.policy.get(self.INJECT)

    @property
    def write_tools(self):
        return self._parameters.policy.get(self.WRITE_TOOLS) is self.SELECTED

    @property
    def priority(self):
        return self._parameters.config.get(self.PRIORITY, self.INACTIVE_PRIORITY)

    @property
    def identifiers(self):
        return self._parameters.config.get(self.IDENTIFIERS)

    @property
    def nested_agent_ids(self):
        return self._parameters.config.get(self.NESTED_AGENT_IDS, [])

    def validate_injection(self):
        if self.inject and self.PRIORITY not in self._parameters.config:
            raise ValueError(self.MISSING_PRIORITY)
