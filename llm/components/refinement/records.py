"""Refinement proposal schema, provenance and lifecycle evidence; no file effects."""

from datetime import datetime, timezone
from enum import StrEnum
from copy import deepcopy
from jsonschema import Draft202012Validator
from llm.services.infrastructure.storage import revision_token
from llm.core.agents import AgentDefinition
from llm.core.history_references import HistoryReferenceRecord
from llm.core.resource_references import ResourceTarget, StoredResourceReference
from llm.core.schema import (
    SchemaKeyword,
    SchemaType,
    SchemaBoolean,
    SchemaExtension,
    OpenSchemaKind,
    object_schema,
    implementation_schema,
    field,
    open_schema,
    metadata_schema,
)
from llm.core.skills import SkillName
from llm.components.naming import ComponentName
from llm.components.skills.records import SkillRecord
from llm.components.prompts.records import PromptDefinition
from llm.components.memory.identity import MemoryIdentity
from .lifecycle import RefinementStatus, TRANSITIONS


class ProposalOperation(StrEnum):
    UPDATE = "update"
    CREATE = "create"
    FORK = "fork"
    BIND_SKILLS = "bind_skills"


class RefinementRecord:
    CAPABILITY = "refinement"
    REVISION_PATTERN = "^[a-f0-9]{64}$"
    MINIMUM_CARDINALITY = 1
    ABSENT = None
    ORIGIN_INDEX = 0
    INPUT_OPERATIONS = (
        ProposalOperation.UPDATE.value,
        ProposalOperation.CREATE.value,
        ProposalOperation.FORK.value,
        ProposalOperation.BIND_SKILLS.value,
    )
    INVALID_INPUT = "Create requires a resource proposal, not lifecycle fields"
    EVALUATOR_OWNER = "external evaluator"
    PATCH_OWNER = "Refinement target EDITABLE and selected target validator"
    SNAPSHOT_OWNER = "selected target record snapshot"
    INVALID_ORIGIN = "Refinement history must preserve its proposal origin"
    INVALID_TRANSITION = "Invalid Refinement history transition"
    INVALID_TIMESTAMP = "Invalid Refinement history timestamp"
    INVALID_STATUS = "Refinement status contradicts its history"
    INVALID_RECEIPT = "Refinement effect receipt contradicts its status"
    MISSING_SKILL_REVISIONS = "Refinement binding requires its original Skill revisions"
    INVALID_SKILL_REVISIONS = "Refinement binding revisions contradict its requested Skills"
    INVALID_PARENT = "Refinement fork requires its original parent revision"
    INVALID_TARGET_REVISION = "Refinement target revision contradicts its operation"

    class Field:
        COMPONENT = ResourceTarget.component.__name__
        IDENTIFIER = ResourceTarget.identifier.__name__
        SESSION_ID = "session_id"
        RUN_ID = "run_id"
        STEP_ID = "step_id"
        TOOL_CALL_ID = "tool_call_id"
        ERROR_CODE = "error_code"
        NOTE = "note"
        VERSION = "version"
        EVALUATOR = "evaluator"
        BASELINE_VERSION = "baseline_version"
        CANDIDATE_VERSION = "candidate_version"
        RESULTS = "results"
        REGRESSIONS = "regressions"
        IMPROVEMENTS = "improvements"
        EVIDENCE = "evidence"
        KIND = "kind"
        PROPOSAL_ID = "proposal_id"
        REASON = "reason"
        METADATA = "metadata"
        STATUS = "status"
        AT = "at"
        SOURCE = "source"
        TARGET = "target"
        OPERATION = "operation"
        EXPECTED_VERSION = "expected_version"
        PARENT = "parent"
        PATCH = "patch"
        BEFORE = "before"
        HISTORY = "history"
        EVALUATIONS = "evaluations"
        APPLIED_TARGET = "applied_target"
        SKILL_VERSIONS = "skill_versions"

    IDENTIFIER = field(SchemaType.STRING, pattern=ComponentName.SCHEMA_PATTERN)

    TARGET = object_schema(
        {
            Field.COMPONENT: field(
                SchemaType.STRING,
                enum=[
                    SkillRecord.CAPABILITY,
                    PromptDefinition.CAPABILITY,
                    AgentDefinition.REFERENCE_COMPONENT,
                    MemoryIdentity.COMPONENT,
                ],
            ),
            Field.IDENTIFIER: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.MIN_LENGTH: MINIMUM_CARDINALITY,
            },
            Field.SESSION_ID: IDENTIFIER,
        },
        required=[Field.COMPONENT, Field.IDENTIFIER],
        additionalProperties=SchemaBoolean.REJECT,
    )

    EVIDENCE = object_schema(
        {
            Field.SESSION_ID: IDENTIFIER,
            Field.RUN_ID: IDENTIFIER,
            Field.STEP_ID: IDENTIFIER,
            Field.TOOL_CALL_ID: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
            Field.ERROR_CODE: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
            Field.NOTE: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
        },
        required=[Field.SESSION_ID, Field.RUN_ID],
        additionalProperties=SchemaBoolean.REJECT,
    )

    PARENT = object_schema(
        {
            Field.IDENTIFIER: SkillName.SCHEMA,
            Field.VERSION: field(SchemaType.STRING, pattern=REVISION_PATTERN),
        },
        required=[Field.IDENTIFIER, Field.VERSION],
        additionalProperties=SchemaBoolean.REJECT,
    )

    EVALUATION = object_schema(
        {
            Field.EVALUATOR: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
            Field.BASELINE_VERSION: field([SchemaType.STRING, SchemaType.NULL]),
            Field.CANDIDATE_VERSION: field(SchemaType.STRING, pattern=REVISION_PATTERN),
            Field.RESULTS: open_schema(EVALUATOR_OWNER, category=OpenSchemaKind.RESULT),
            Field.REGRESSIONS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaExtension.OWNER: EVALUATOR_OWNER},
            Field.IMPROVEMENTS: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaExtension.OWNER: EVALUATOR_OWNER,
            },
            Field.EVIDENCE: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.ITEMS: EVIDENCE},
        },
        required=[
            Field.EVALUATOR,
            Field.BASELINE_VERSION,
            Field.CANDIDATE_VERSION,
            Field.RESULTS,
            Field.REGRESSIONS,
            Field.IMPROVEMENTS,
        ],
        additionalProperties=SchemaBoolean.REJECT,
    )

    SOURCE = object_schema(
        {
            Field.KIND: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
            Field.SESSION_ID: IDENTIFIER,
            Field.RUN_ID: IDENTIFIER,
            Field.STEP_ID: IDENTIFIER,
            Field.PROPOSAL_ID: IDENTIFIER,
            Field.REASON: field(SchemaType.STRING),
            Field.METADATA: metadata_schema(),
        },
        required=[Field.KIND],
    )

    HISTORY = object_schema(
        {Field.STATUS: field(SchemaType.STRING), Field.AT: field(SchemaType.STRING), Field.SOURCE: SOURCE},
        required=[Field.STATUS, Field.AT, Field.SOURCE],
    )

    STORED_EVALUATION = object_schema(
        {**EVALUATION[SchemaKeyword.PROPERTIES], Field.SOURCE: SOURCE, Field.AT: field(SchemaType.STRING)},
        required=[*EVALUATION[SchemaKeyword.REQUIRED], Field.SOURCE, Field.AT],
    )

    SCHEMA = object_schema(
        {
            Field.TARGET: TARGET,
            Field.OPERATION: {
                SchemaKeyword.ENUM: [
                    ProposalOperation.UPDATE.value,
                    ProposalOperation.CREATE.value,
                    ProposalOperation.FORK.value,
                    ProposalOperation.BIND_SKILLS.value,
                ]
            },
            Field.EXPECTED_VERSION: field(
                [SchemaType.STRING, SchemaType.NULL], minLength=MINIMUM_CARDINALITY
            ),
            Field.PARENT: PARENT,
            Field.REASON: field(SchemaType.STRING, minLength=MINIMUM_CARDINALITY),
            Field.EVIDENCE: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaKeyword.MIN_ITEMS: MINIMUM_CARDINALITY,
                SchemaKeyword.ITEMS: EVIDENCE,
            },
            Field.PATCH: open_schema(
                PATCH_OWNER, category=OpenSchemaKind.IMPLEMENTATION, minProperties=MINIMUM_CARDINALITY
            ),
            Field.STATUS: field(SchemaType.STRING, enum=list(RefinementStatus)),
            Field.BEFORE: open_schema(SNAPSHOT_OWNER, category=OpenSchemaKind.IMPLEMENTATION),
            Field.METADATA: metadata_schema(),
            Field.SOURCE: SOURCE,
            Field.HISTORY: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaKeyword.ITEMS: HISTORY,
                SchemaKeyword.MIN_ITEMS: MINIMUM_CARDINALITY,
            },
            Field.EVALUATIONS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.ITEMS: STORED_EVALUATION},
            Field.APPLIED_TARGET: object_schema(
                {
                    Field.IDENTIFIER: TARGET[SchemaKeyword.PROPERTIES][Field.IDENTIFIER],
                    Field.VERSION: field(SchemaType.STRING, pattern=REVISION_PATTERN),
                },
                required=[Field.IDENTIFIER, Field.VERSION],
            ),
            Field.SKILL_VERSIONS: object_schema(
                additionalProperties=field(SchemaType.STRING, pattern=REVISION_PATTERN)
            ),
        },
        required=[
            Field.TARGET,
            Field.OPERATION,
            Field.EXPECTED_VERSION,
            Field.REASON,
            Field.EVIDENCE,
            Field.PATCH,
            Field.STATUS,
            Field.BEFORE,
            Field.SOURCE,
            Field.HISTORY,
            Field.EVALUATIONS,
        ],
    )

    def __init__(self, value):
        # A projection of admitted input or a Component-validated stored record.
        # Persisted validation remains validate_history after the selected schema.
        self._value = value

    @classmethod
    def from_input(cls, data):
        allowed = {
            cls.Field.TARGET,
            cls.Field.OPERATION,
            cls.Field.EXPECTED_VERSION,
            cls.Field.REASON,
            cls.Field.EVIDENCE,
            cls.Field.PATCH,
            cls.Field.METADATA,
            cls.Field.PARENT,
        }
        required = {
            cls.Field.TARGET,
            cls.Field.OPERATION,
            cls.Field.EXPECTED_VERSION,
            cls.Field.REASON,
            cls.Field.EVIDENCE,
            cls.Field.PATCH,
        }
        if (
            not isinstance(data, dict)
            or set(data) - allowed
            or not required <= data.keys()
            or data.get(cls.Field.OPERATION) not in cls.INPUT_OPERATIONS
            or not isinstance(data[cls.Field.PATCH], dict)
        ):
            raise ValueError(cls.INVALID_INPUT)
        return cls(data)

    @property
    def target(self):
        return RefinementTarget(self._value[self.Field.TARGET])

    @property
    def parent(self):
        return RefinementRevision(self._value[self.Field.PARENT])

    @property
    def optional_parent(self):
        return self._value.get(self.Field.PARENT)

    @property
    def has_parent(self):
        return self.Field.PARENT in self._value

    @property
    def applied_target(self):
        return RefinementRevision(self._value[self.Field.APPLIED_TARGET])

    @property
    def optional_evaluations(self):
        return self._value.get(self.Field.EVALUATIONS)

    @property
    def optional_evidence(self):
        return self._value.get(self.Field.EVIDENCE)

    @property
    def operation(self):
        return self._value[self.Field.OPERATION]

    @property
    def expected_version(self):
        return self._value[self.Field.EXPECTED_VERSION]

    @property
    def status(self):
        return self._value[self.Field.STATUS]

    @property
    def evidence(self):
        return self._value[self.Field.EVIDENCE]

    @property
    def patch(self):
        return self._value[self.Field.PATCH]

    @property
    def requested_skills(self):
        return self.patch.get(AgentDefinition.ResourceField.SKILLS)

    @property
    def has_only_skill_binding(self):
        return set(self.patch) == {AgentDefinition.ResourceField.SKILLS}

    @property
    def skill_versions(self):
        return self._value[self.Field.SKILL_VERSIONS]

    @property
    def before(self):
        return self._value[self.Field.BEFORE]

    def proposed(self, before, source_factory):
        return type(self)(
            {
                **deepcopy(self._value),
                self.Field.BEFORE: before,
                self.Field.STATUS: RefinementStatus.PROPOSED,
                self.Field.SOURCE: source_factory(),
                self.Field.HISTORY: [],
                self.Field.EVALUATIONS: [],
            }
        )

    def record_skill_versions(self, versions):
        self._value[self.Field.SKILL_VERSIONS] = versions

    def record_origin(self):
        self._value[self.Field.HISTORY].append(
            {
                self.Field.STATUS: RefinementStatus.PROPOSED,
                self.Field.AT: datetime.now(timezone.utc).isoformat(),
                self.Field.SOURCE: self._value[self.Field.SOURCE],
            }
        )

    def transitioned(self, status, source, applied_target=ABSENT):
        value = deepcopy(self._value)
        if applied_target is not self.ABSENT:
            value[self.Field.APPLIED_TARGET] = deepcopy(applied_target)
        value[self.Field.STATUS] = status
        value[self.Field.HISTORY].append(
            {
                self.Field.STATUS: status,
                self.Field.AT: datetime.now(timezone.utc).isoformat(),
                self.Field.SOURCE: deepcopy(source),
            }
        )
        return type(self)(value)

    def evaluated(self, evaluation, source_factory):
        value = deepcopy(self._value)
        value[self.Field.EVALUATIONS].append(
            {
                **deepcopy(evaluation),
                self.Field.SOURCE: source_factory(),
                self.Field.AT: datetime.now(timezone.utc).isoformat(),
            }
        )
        return type(self)(value)

    def to_dict(self):
        return self._value

    @classmethod
    def validate_history(cls, data):
        history = data[RefinementRecord.Field.HISTORY]
        if (
            history[RefinementRecord.ORIGIN_INDEX][RefinementRecord.Field.STATUS] != RefinementStatus.PROPOSED
            or history[RefinementRecord.ORIGIN_INDEX][RefinementRecord.Field.SOURCE]
            != data[RefinementRecord.Field.SOURCE]
        ):
            raise ValueError(RefinementRecord.INVALID_ORIGIN)
        previous_status, previous_at = (RefinementRecord.ABSENT, RefinementRecord.ABSENT)
        for entry in history:
            status = entry[RefinementRecord.Field.STATUS]
            if status not in TRANSITIONS or (
                previous_status is not RefinementRecord.ABSENT and status not in TRANSITIONS[previous_status]
            ):
                raise ValueError(RefinementRecord.INVALID_TRANSITION)
            at = datetime.fromisoformat(entry[RefinementRecord.Field.AT])
            if at.tzinfo is RefinementRecord.ABSENT or (
                previous_at is not RefinementRecord.ABSENT and at < previous_at
            ):
                raise ValueError(RefinementRecord.INVALID_TIMESTAMP)
            previous_status, previous_at = (status, at)
        if previous_status != data[RefinementRecord.Field.STATUS]:
            raise ValueError(RefinementRecord.INVALID_STATUS)
        if (RefinementRecord.Field.APPLIED_TARGET in data) != (
            data[RefinementRecord.Field.STATUS] in {RefinementStatus.APPLIED, RefinementStatus.ROLLED_BACK}
        ):
            raise ValueError(RefinementRecord.INVALID_RECEIPT)
        if (RefinementRecord.Field.SKILL_VERSIONS in data) != (
            data[RefinementRecord.Field.OPERATION] == ProposalOperation.BIND_SKILLS.value
        ):
            raise ValueError(RefinementRecord.MISSING_SKILL_REVISIONS)
        if data[RefinementRecord.Field.OPERATION] == ProposalOperation.BIND_SKILLS.value and set(
            data[RefinementRecord.Field.SKILL_VERSIONS]
        ) != set(data[RefinementRecord.Field.PATCH].get(AgentDefinition.ResourceField.SKILLS.value, [])):
            raise ValueError(RefinementRecord.INVALID_SKILL_REVISIONS)
        if (RefinementRecord.Field.PARENT in data) != (
            data[RefinementRecord.Field.OPERATION] == ProposalOperation.FORK.value
        ):
            raise ValueError(RefinementRecord.INVALID_PARENT)
        creating = data[RefinementRecord.Field.OPERATION] in {
            ProposalOperation.CREATE.value,
            ProposalOperation.FORK.value,
        }
        if creating != (data[RefinementRecord.Field.EXPECTED_VERSION] is RefinementRecord.ABSENT):
            raise ValueError(RefinementRecord.INVALID_TARGET_REVISION)

    @classmethod
    def history_references(cls, records):
        return HistoryReferenceRecord.project(
            message_ids=[],
            session_ids=[],
            run_ids=sorted(
                {
                    ref[RefinementRecord.Field.RUN_ID]
                    for record in records
                    for ref in [
                        *record[RefinementRecord.Field.EVIDENCE],
                        record.get(RefinementRecord.Field.SOURCE, {}),
                        *(
                            ref
                            for evaluation in record.get(RefinementRecord.Field.EVALUATIONS, [])
                            for ref in evaluation.get(RefinementRecord.Field.EVIDENCE, [])
                        ),
                        *(
                            evaluation.get(RefinementRecord.Field.SOURCE, {})
                            for evaluation in record.get(RefinementRecord.Field.EVALUATIONS, [])
                        ),
                    ]
                    if RefinementRecord.Field.RUN_ID in ref
                }
            ),
        )

    @classmethod
    def references(cls, records):
        """Agent proposals retain their target identity until the proposal is removed."""
        return [
            StoredResourceReference.from_target(
                ResourceTarget(
                    AgentDefinition.REFERENCE_COMPONENT,
                    value[RefinementRecord.Field.TARGET][RefinementRecord.Field.IDENTIFIER],
                ),
                identifier,
            ).to_dict()
            for identifier, value in records.items()
            if value[RefinementRecord.Field.TARGET][RefinementRecord.Field.COMPONENT]
            == AgentDefinition.REFERENCE_COMPONENT
        ]


class RefinementPolicy:
    EXPLICITLY_ENABLED = True
    NO_PROPOSAL_OPERATIONS = ()

    class Field:
        APPLY_TOOLS = "apply_tools"
        REQUIRE_EVALUATION = "require_evaluation"
        PROPOSAL_OPERATIONS = "proposal_operations"

    @classmethod
    def schema(cls):
        return implementation_schema(
            config=object_schema(additionalProperties=SchemaBoolean.REJECT),
            policy=object_schema(
                {
                    cls.Field.APPLY_TOOLS: field(SchemaType.BOOLEAN),
                    cls.Field.REQUIRE_EVALUATION: field(SchemaType.BOOLEAN),
                    cls.Field.PROPOSAL_OPERATIONS: {
                        SchemaKeyword.TYPE: SchemaType.ARRAY,
                        SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                        SchemaKeyword.ITEMS: {
                            SchemaKeyword.ENUM: [
                                ProposalOperation.UPDATE.value,
                                ProposalOperation.CREATE.value,
                                ProposalOperation.FORK.value,
                                ProposalOperation.BIND_SKILLS.value,
                            ]
                        },
                    },
                },
                additionalProperties=SchemaBoolean.REJECT,
            ),
        )

    @classmethod
    def apply_tools(cls, policy):
        return policy.get(cls.Field.APPLY_TOOLS) is cls.EXPLICITLY_ENABLED

    @classmethod
    def proposal_operations(cls, policy):
        return policy.get(cls.Field.PROPOSAL_OPERATIONS, cls.NO_PROPOSAL_OPERATIONS)

    @classmethod
    def requires_evaluation(cls, policy):
        return policy.get(cls.Field.REQUIRE_EVALUATION) is cls.EXPLICITLY_ENABLED


class RefinementSource:
    TOOL = "tool"
    API = "api"
    APPLY = RefinementRecord.CAPABILITY
    ROLLBACK = "refinement_rollback"
    ABSENT = None
    INVALID = "Proposal source must be an object"
    Field = RefinementRecord.Field

    def __init__(self, value):
        self._value = value

    @classmethod
    def from_input(cls, source):
        value = deepcopy(source) if source is not cls.ABSENT else {cls.Field.KIND: cls.API}
        if not isinstance(value, dict):
            raise ValueError(cls.INVALID)
        return cls(value)

    @property
    def has_run(self):
        return self.Field.RUN_ID in self._value

    @property
    def run_id(self):
        return self._value[self.Field.RUN_ID]

    @property
    def session_id(self):
        return self._value.get(self.Field.SESSION_ID)

    @property
    def step_id(self):
        return self._value.get(self.Field.STEP_ID)

    @classmethod
    def proposal(cls, source, kind, identifier):
        return {**source, cls.Field.KIND: kind, cls.Field.PROPOSAL_ID: identifier}

    @classmethod
    def failed(cls, source, reason):
        return {**source, cls.Field.REASON: reason}

    def to_dict(self):
        return self._value

    @classmethod
    def tool(cls, call):
        return {
            cls.Field.KIND: cls.TOOL,
            cls.Field.SESSION_ID: call.session_id,
            cls.Field.RUN_ID: call.run_id,
            cls.Field.STEP_ID: call.step_id,
        }


class RefinementTarget:
    """Typed projection of a proposal target; its schema remains RefinementRecord.TARGET."""

    Field = RefinementRecord.Field
    INVALID = "Refinement target must be a saved Project resource"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read(cls, value):
        if not Draft202012Validator(RefinementRecord.TARGET).is_valid(value):
            raise ValueError(cls.INVALID)
        return cls(value)

    @property
    def has_session(self):
        return self.Field.SESSION_ID in self._value

    @property
    def component(self):
        return self._value[self.Field.COMPONENT]

    @property
    def identifier(self):
        return self._value[self.Field.IDENTIFIER]

    def session_id_or(self, default):
        return self._value.get(self.Field.SESSION_ID, default)

    def session_arguments(self):
        return (
            {self.Field.SESSION_ID: self._value[self.Field.SESSION_ID]}
            if self.Field.SESSION_ID in self._value
            else {}
        )

    def to_dict(self):
        return self._value


class RefinementToolInput:
    """Projection after the Tool's schema check; it grants no execution authority."""

    def __init__(self, value):
        self._value = value

    @property
    def target(self):
        return RefinementTarget(self._value[RefinementRecord.Field.TARGET])

    def target_session(self, default):
        return self.target.session_id_or(default)

    @property
    def operation(self):
        return self._value[RefinementRecord.Field.OPERATION]

    def has_foreign_evidence(self, session_id):
        return any(
            ref[RefinementRecord.Field.SESSION_ID] != session_id
            for ref in self._value[RefinementRecord.Field.EVIDENCE]
        )


class RefinementRevision:
    Field = RefinementRecord.Field
    INVALID_PARENT = "Fork requires parent identifier and version"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read_parent(cls, value):
        if not Draft202012Validator(RefinementRecord.PARENT).is_valid(value):
            raise ValueError(cls.INVALID_PARENT)
        return cls(value)

    @classmethod
    def create(cls, identifier, version):
        return cls({cls.Field.IDENTIFIER: identifier, cls.Field.VERSION: version})

    @property
    def identifier(self):
        return self._value[self.Field.IDENTIFIER]

    @property
    def version(self):
        return self._value[self.Field.VERSION]

    def to_dict(self):
        return self._value


class RefinementValidation:
    VALID = "valid"
    TARGET_VERSION = "target_version"
    SUCCESS = True
    NO_BASELINE = None
    Field = RefinementRecord.Field

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, expected_version, current, candidate):
        return cls(
            {
                cls.VALID: cls.SUCCESS,
                cls.TARGET_VERSION: expected_version,
                cls.Field.BASELINE_VERSION: revision_token(current) if current else cls.NO_BASELINE,
                cls.Field.CANDIDATE_VERSION: revision_token(candidate),
            }
        )

    @property
    def baseline_version(self):
        return self._value[self.Field.BASELINE_VERSION]

    @property
    def candidate_version(self):
        return self._value[self.Field.CANDIDATE_VERSION]

    def to_dict(self):
        return self._value


class RefinementEvaluation:
    Field = RefinementRecord.Field
    INVALID = "Invalid refinement evaluation"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read(cls, value):
        if not Draft202012Validator(RefinementRecord.EVALUATION).is_valid(value):
            raise ValueError(cls.INVALID)
        return cls(value)

    def matches(self, validation):
        return (
            self._value[self.Field.BASELINE_VERSION] == validation.baseline_version
            and self._value[self.Field.CANDIDATE_VERSION] == validation.candidate_version
        )

    @property
    def has_evidence(self):
        return self._value.get(self.Field.EVIDENCE)

    @property
    def evidence(self):
        return self._value[self.Field.EVIDENCE]

    def to_dict(self):
        return self._value


class RefinementEvidence:
    Field = RefinementRecord.Field
    REQUIRED = "Proposal requires Run evidence"
    INVALID = "Invalid proposal evidence"
    TOOL_MISMATCH = "Evidence Tool call does not match its Step"
    ERROR_MISMATCH = "Evidence error code does not match its source"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read_all(cls, evidence):
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(cls.REQUIRED)
        for ref in evidence:
            if not Draft202012Validator(RefinementRecord.EVIDENCE).is_valid(ref):
                raise ValueError(cls.INVALID)
            yield cls(ref)

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @property
    def run_id(self):
        return self._value[self.Field.RUN_ID]

    @property
    def step_id(self):
        return self._value.get(self.Field.STEP_ID)

    def check_reference(self, reference):
        if (
            self.Field.TOOL_CALL_ID in self._value
            and reference.tool_call_id != self._value[self.Field.TOOL_CALL_ID]
        ):
            raise ValueError(self.TOOL_MISMATCH)
        if (
            self.Field.ERROR_CODE in self._value
            and reference.error_code != self._value[self.Field.ERROR_CODE]
        ):
            raise ValueError(self.ERROR_MISMATCH)
