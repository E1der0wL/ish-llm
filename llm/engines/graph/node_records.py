"""Graph-owned payload declarations, Writers and projections; no storage or policy defaults."""

from llm.core.checkpoint_records import CheckpointStep, CheckpointRecords
from llm.core.checkpoints import CheckpointLifecycle
from llm.core.interactions import InteractionRequest
from llm.core.step_event_metadata import StepEventMetadata
from llm.components.workflows.bindings import WorkflowBindings


class GraphDecision:
    """Graph continuation input; it is not a Tool execution authorization."""

    CONFIRM_EMPTY = True
    APPROVED = "approved"
    STATE = "state"
    REJECTED = False

    def __init__(self, value):
        self._value = value

    @property
    def has_approved(self):
        return self.APPROVED in self._value

    @property
    def has_state(self):
        return self.STATE in self._value

    @property
    def extra_fields(self):
        return self._value.keys() - {self.APPROVED, self.STATE}

    @property
    def rejected(self):
        return self._value.get(self.APPROVED) is self.REJECTED

    @property
    def approved(self):
        return self._value[self.APPROVED]

    @property
    def optional_approved(self):
        return self._value.get(self.APPROVED)

    @property
    def state(self):
        return self._value[self.STATE]

    @property
    def state_or_empty(self):
        return self._value.get(self.STATE, {})


class GraphNodeRecord:
    """Graph-private checkpoint payload; common lifecycle field declarations are reused."""

    STATUS = CheckpointStep.Field.STATUS.value
    INTERACTION = CheckpointStep.Field.INTERACTION.value
    REQUIRES_RETRY = CheckpointStep.Field.REQUIRES_RETRY.value
    CONTAINER = CheckpointLifecycle.container.__name__
    PATH = "path"
    NODE_ID = "node_id"
    NODE_TYPE = "node_type"
    STEP_ID = "step_id"
    SOURCE_RUN_ID = "source_run_id"
    INPUT_STATE = "input_state"
    DECISION = "decision"
    OUTPUT = "output"
    PORT = "port"
    APPROVAL_REQUIRED = "approval_required"
    PROMPT = "prompt"
    RESUME_SCHEMA = WorkflowBindings.RESUME_SCHEMA
    NOT_CONTAINER = False
    APPROVAL_REQUIRED_VALUE = True

    def __init__(self, value):
        self._value = value

    @property
    def path(self):
        return self._value[self.PATH]

    @property
    def node_id(self):
        return self._value[self.NODE_ID]

    @property
    def node_type(self):
        return self._value[self.NODE_TYPE]

    @property
    def step_id(self):
        return self._value[self.STEP_ID]

    @property
    def source_run_id(self):
        return self._value[self.SOURCE_RUN_ID]

    @property
    def input_state(self):
        return self._value[self.INPUT_STATE]

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def output(self):
        return self._value[self.OUTPUT]

    @property
    def port(self):
        return self._value[self.PORT]

    @property
    def decision(self):
        return self._value[self.DECISION]

    @property
    def optional_status(self):
        return self._value.get(self.STATUS)

    @property
    def container(self):
        return self._value.get(self.CONTAINER)

    @property
    def approval_required(self):
        return self._value.get(self.APPROVAL_REQUIRED)

    @property
    def resume_schema(self):
        return self._value.get(self.RESUME_SCHEMA)

    @property
    def non_container(self):
        return not self._value.get(self.CONTAINER, self.NOT_CONTAINER)

    @property
    def required_container(self):
        return self._value[self.CONTAINER]

    @property
    def present(self):
        return bool(self._value)

    @property
    def decision_or_empty(self):
        return (self._value or {}).get(self.DECISION, {})

    @property
    def has_interaction(self):
        return self.INTERACTION in self._value

    @property
    def interaction(self):
        return InteractionRequest.from_dict(self._value[self.INTERACTION])

    @classmethod
    def attach_decision(cls, info, decision):
        info[cls.DECISION] = decision

    @classmethod
    def create(cls, path, node_id, node_type, step_id, source_run_id, input_state, container):
        return {
            cls.PATH: path,
            cls.NODE_ID: node_id,
            cls.NODE_TYPE: node_type,
            cls.STEP_ID: step_id,
            cls.SOURCE_RUN_ID: source_run_id,
            cls.INPUT_STATE: input_state,
            cls.CONTAINER: container,
        }

    @classmethod
    def started(cls, extension, status, requires_retry):
        return {**extension, cls.STATUS: status, cls.REQUIRES_RETRY: requires_retry}

    @classmethod
    def completed(cls, extension, status, output, port):
        return {**extension, cls.STATUS: status, cls.OUTPUT: output, cls.PORT: port}

    @classmethod
    def waiting_confirmation(cls, extension, status, resume_schema, interaction):
        return {
            **extension,
            cls.STATUS: status,
            cls.RESUME_SCHEMA: resume_schema,
            cls.INTERACTION: interaction,
        }

    @classmethod
    def waiting_approval(cls, extension, status, approval_required, prompt, interaction):
        return {
            **extension,
            cls.STATUS: status,
            cls.APPROVAL_REQUIRED: approval_required,
            cls.PROMPT: prompt,
            cls.INTERACTION: interaction,
        }

    @classmethod
    def selection_checkpoint(cls, records):
        return {CheckpointRecords.records.__name__: records}


class GraphCheckpointHeader:
    """Graph header payload; conversation evidence remains owned by CheckpointConversation."""

    FORMAT = "format"
    BINDING = "binding"
    INITIAL_STATE = "initial_state"

    def __init__(self, value):
        self._value = value

    @property
    def binding(self):
        return self._value[self.BINDING]

    @property
    def initial_state(self):
        return self._value[self.INITIAL_STATE]

    @property
    def format(self):
        return self._value.get(self.FORMAT)

    @classmethod
    def create(cls, format, binding, initial_state, extension):
        return {cls.FORMAT: format, cls.BINDING: binding, cls.INITIAL_STATE: initial_state, **extension}


class GraphStepMetadata:
    """Graph Step observations and display ancestry; no execution authority."""

    PARENT_STEP_ID = StepEventMetadata.PARENT_STEP_ID
    WORKFLOW_ID = "workflow_id"
    PATH = GraphNodeRecord.PATH
    INPUTS = WorkflowBindings.INPUTS
    NODE_CHECKPOINT_KEY = "node_checkpoint_key"
    NODE_PATH = "node_path"
    CHECKPOINT_KEY = "checkpoint_key"
    REUSED_FROM_RUN = "reused_from_run"
    REUSED_STEP_ID = "reused_step_id"
    REUSED = StepEventMetadata.REUSED
    REUSED_VALUE = StepEventMetadata.REUSED_VALUE
    NODE_ID = GraphNodeRecord.NODE_ID
    NODE_TYPE = GraphNodeRecord.NODE_TYPE
    DEFINITION = "definition"
    VISITED_NODES = "visited_nodes"
    RUNTIME = "runtime"
    PORT = GraphNodeRecord.PORT
    ITERATIONS = "iterations"
    INPUT = "input"
    RESULT = "result"
    CHECKPOINT_NAME = StepEventMetadata.CHECKPOINT_NAME

    def __init__(self, value):
        self._value = value

    @property
    def reused_step_id(self):
        return self._value.get(self.REUSED_STEP_ID)

    @property
    def node_checkpoint_key(self):
        return self._value.get(self.NODE_CHECKPOINT_KEY)

    @classmethod
    def add_port(cls, detail, port):
        detail[cls.PORT] = port

    @classmethod
    def add_iterations(cls, detail, iterations):
        detail[cls.ITERATIONS] = iterations

    @classmethod
    def add_result(cls, detail, inputs, result):
        detail.update({cls.INPUT: inputs, cls.RESULT: result})

    @classmethod
    def nested_child(cls, extension, parent_step_id, workflow_id):
        return {
            **extension,
            cls.PARENT_STEP_ID: extension.get(cls.PARENT_STEP_ID, parent_step_id),
            cls.WORKFLOW_ID: extension.get(cls.WORKFLOW_ID, workflow_id),
        }

    @classmethod
    def nested(cls, parent_step_id, workflow_id, path, inputs):
        return {
            cls.PARENT_STEP_ID: parent_step_id,
            cls.WORKFLOW_ID: workflow_id,
            cls.PATH: path,
            cls.INPUTS: inputs,
        }

    @classmethod
    def node(cls, node_id, path, node_type, definition, checkpoint_key):
        return {
            cls.NODE_ID: node_id,
            cls.PATH: path,
            cls.NODE_TYPE: node_type,
            cls.DEFINITION: definition,
            cls.CHECKPOINT_KEY: checkpoint_key,
        }

    @classmethod
    def root(cls, workflow_id, definition):
        return {cls.WORKFLOW_ID: workflow_id, cls.DEFINITION: definition}

    @classmethod
    def completed(cls, visited_nodes, runtime):
        return {cls.VISITED_NODES: visited_nodes, cls.RUNTIME: runtime}

    @classmethod
    def reused_node(cls, extension, checkpoint_key, reused_from_run, reused_step_id):
        return {
            **extension,
            cls.CHECKPOINT_KEY: checkpoint_key,
            cls.REUSED_FROM_RUN: reused_from_run,
            cls.REUSED_STEP_ID: reused_step_id,
        }

    @classmethod
    def reused_completion(cls, reused):
        return {cls.REUSED: reused}

    @classmethod
    def node_child(cls, parent_step_id, node_checkpoint_key, node_path, extension):
        return {
            cls.PARENT_STEP_ID: parent_step_id,
            cls.NODE_CHECKPOINT_KEY: node_checkpoint_key,
            cls.NODE_PATH: node_path,
            **extension,
        }

    @classmethod
    def tool(cls, node_id, checkpoint_name):
        return {cls.NODE_ID: node_id, cls.CHECKPOINT_NAME: checkpoint_name}


class GraphInput:
    """Initial message binding input; user data stays opaque."""

    PROMPT = "prompt"
    MESSAGE_ID = "message_id"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, prompt, message_id):
        return {cls.PROMPT: prompt, cls.MESSAGE_ID: message_id}


class GraphConfirmation:
    """Explicit pre-node confirmation provenance and action."""

    TITLE = "Workflow 노드 실행 확인"
    CATEGORY = "workflow.continue"
    PROJECT_ID = "project_id"
    SESSION_ID = "session_id"
    RUN_ID = "run_id"
    NODE_ID = GraphNodeRecord.NODE_ID
    PATH = GraphNodeRecord.PATH
    DEFINITION = "definition"

    def __init__(self, value):
        self._value = value

    @classmethod
    def source(cls, project_id, session_id, run_id, node_id, path):
        return {
            cls.PROJECT_ID: project_id,
            cls.SESSION_ID: session_id,
            cls.RUN_ID: run_id,
            cls.NODE_ID: node_id,
            cls.PATH: path,
        }

    @classmethod
    def action(cls, definition):
        return {cls.DEFINITION: definition}
