"""Host authoring Tool input, schema and response contracts; no execution authority."""

from dataclasses import dataclass

from llm.core.agent_views import AgentRevision
from llm.core.agents import AgentDefinition
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from llm.components.goals.records import GoalDefinition
from llm.components.goals.tool_values import GoalToolArguments
from llm.components.rag.document_records import RAGDocumentInput


class ResourceToolContract:
    REVISION = "1"
    READ_ONLY = False
    WRITES = True
    OPERATION_KEY_REQUIRED = False
    ABSENT = None
    MISSING_OWNER = "Resource tools require an owning Run in this Project"

    @classmethod
    def parameters(cls, properties, required):
        return {
            SchemaKeyword.TYPE: SchemaType.OBJECT,
            SchemaKeyword.PROPERTIES: properties,
            SchemaKeyword.REQUIRED: required,
            SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        }


class DefinitionArguments:
    IDENTIFIER = "identifier"
    DEFINITION = "definition"
    IDENTIFIER_SCHEMA = {
        SchemaKeyword.TYPE: SchemaType.STRING,
        SchemaKeyword.PATTERN: "^[A-Za-z0-9_-]{1,64}$",
    }

    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[self.IDENTIFIER]

    @property
    def definition(self):
        return self._value[self.DEFINITION]

    @classmethod
    def create_properties(cls, identifier_schema):
        return {
            cls.IDENTIFIER: identifier_schema or cls.IDENTIFIER_SCHEMA,
            cls.DEFINITION: {SchemaKeyword.TYPE: SchemaType.OBJECT},
        }

    @classmethod
    def create_required(cls):
        return [cls.IDENTIFIER, cls.DEFINITION]

    @classmethod
    def read_properties(cls):
        return {cls.IDENTIFIER: cls.IDENTIFIER_SCHEMA}

    @classmethod
    def read_required(cls):
        return [cls.IDENTIFIER]


@dataclass(frozen=True, slots=True)
class ResourceAuthorship:
    KEY = "hub_source"
    DEFAULT_FIELD = AgentDefinition.Field.METADATA.value

    session_id: str
    run_id: str
    step_id: str

    def to_dict(self):
        cls = type(self)
        return {
            cls.session_id.__name__: self.session_id,
            cls.run_id.__name__: self.run_id,
            cls.step_id.__name__: self.step_id,
        }

    @classmethod
    def stamp(cls, value, source_field, source):
        value.setdefault(source_field, {})[cls.KEY] = cls(
            source.session_id, source.run_id, source.step_id
        ).to_dict()


@dataclass(frozen=True, slots=True)
class AuthoredDefinition:
    created: str
    component: str
    applies_to: str = AgentRevision.SUBSEQUENT_RUNS

    def to_dict(self):
        cls = type(self)
        return {
            cls.created.__name__: self.created,
            cls.component.__name__: self.component,
            cls.applies_to.__name__: self.applies_to,
        }


class DefinitionPreview:
    """The optional field subset of the authoring list response, not a stored Reader."""

    TITLE = "title"
    FIELDS = (
        TITLE,
        AgentDefinition.Field.PURPOSE.value,
        AgentDefinition.Field.DESCRIPTION.value,
        AgentDefinition.Field.ENGINE.value,
    )

    @classmethod
    def project(cls, records):
        return {
            key: {name: value[name] for name in cls.FIELDS if name in value}
            for key, value in records.items()
        }


class AuthoringTool:
    DISCOVER = True
    SCHEMA_NAME = "{prefix}_schema"
    CREATE_NAME = "{prefix}_create"
    LIST_NAME = "{prefix}_list"
    READ_NAME = "{prefix}_read"
    SCHEMA_DESCRIPTION = (
        "Read the exact creation input schema before creating a {prefix}."
    )
    CREATE_DESCRIPTION = (
        "Create a new project {prefix}. Read {schema_tool} first. Existing IDs are never overwritten. "
        "Saved execution definitions become available to subsequent Runs; creation does not execute or grant new authority."
    )
    LIST_DESCRIPTION = "List saved {name} in this Project."
    READ_DESCRIPTION = "Read a saved definition and its revision as reference data."

    @classmethod
    def creation_description(cls, prefix):
        return cls.CREATE_DESCRIPTION.format(
            prefix=prefix, schema_tool=cls.SCHEMA_NAME.format(prefix=prefix)
        )


class WorkflowAuthoring:
    HANDLER_EXTENSION = "x-hub-action-handlers"

    @classmethod
    def extensions(cls, handlers):
        return {cls.HANDLER_EXTENSION: handlers}


class RAGAuthoring:
    NAME = "rag_add_document"
    DESCRIPTION = (
        "Register a document in this Project's RAG corpus and build its search index. "
        "Use file_read to read an authorized file first, then provide its text. Embedding "
        "configuration is required. Existing IDs are not overwritten. Report success only after this returns."
    )
    DESCRIPTION_HINT = "Short factual overview for deciding relevance before retrieval."
    CONTENT_MINIMUM = 1

    @classmethod
    def properties(cls):
        field = RAGDocumentInput.Field
        return {
            field.IDENTIFIER: DefinitionArguments.IDENTIFIER_SCHEMA,
            field.TITLE: {SchemaKeyword.TYPE: SchemaType.STRING},
            field.CONTENT: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.MIN_LENGTH: cls.CONTENT_MINIMUM,
            },
            field.DESCRIPTION: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.DESCRIPTION: cls.DESCRIPTION_HINT,
            },
            field.METADATA: {SchemaKeyword.TYPE: SchemaType.OBJECT},
        }

    @classmethod
    def required(cls):
        field = RAGDocumentInput.Field
        return [field.IDENTIFIER, field.TITLE, field.CONTENT]


class GoalAuthoring:
    CREATE_NAME = "goal_create"
    COMPLETE_NAME = "goal_complete"
    RELATION = "contributes_to"
    CREATE_DESCRIPTION = (
        "Track an authorized multi-step task in this Session with observable success criteria. "
        "Reuse existing goals; do not create goals for casual questions or invent new tasks."
    )
    COMPLETE_DESCRIPTION = (
        "Mark an existing goal complete only after verifying all its success criteria. "
        "Does not change or restart any Run."
    )
    MINIMUM_CRITERIA = 1

    @classmethod
    def create_properties(cls):
        field = GoalDefinition.Field
        return {
            GoalToolArguments.IDENTIFIER: DefinitionArguments.IDENTIFIER_SCHEMA,
            field.TITLE: GoalToolArguments.text_schema(),
            field.OBJECTIVE: GoalToolArguments.text_schema(),
            field.SUCCESS_CRITERIA: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaKeyword.MIN_ITEMS: cls.MINIMUM_CRITERIA,
                SchemaKeyword.ITEMS: GoalToolArguments.text_schema(),
            },
        }

    @classmethod
    def create_required(cls):
        field = GoalDefinition.Field
        return [
            GoalToolArguments.IDENTIFIER,
            field.TITLE,
            field.OBJECTIVE,
            field.SUCCESS_CRITERIA,
        ]

    @classmethod
    def complete_properties(cls):
        return {
            GoalToolArguments.IDENTIFIER: DefinitionArguments.IDENTIFIER_SCHEMA,
            GoalToolArguments.EXPECTED_VERSION: GoalToolArguments.text_schema(),
        }

    @classmethod
    def complete_required(cls):
        return [GoalToolArguments.IDENTIFIER, GoalToolArguments.EXPECTED_VERSION]
