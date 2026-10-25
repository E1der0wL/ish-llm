"""RAG Job planning projections; observing a plan never grants execution."""

from llm.core.contracts import ResourceRef, OperationProgress
from llm.core.plans import CheckpointPlan
from .job_records import JobRecord
from llm.services.infrastructure.storage import revision_token


class JobPlanEvidence:
    SOURCE = "source"
    JOB = "job"
    FILES = "files"
    CHECKPOINTS = "checkpoints"
    LIVE = "live"

    @classmethod
    def checkpoint(cls, source, job, files, checkpoints, live):
        return {
            cls.SOURCE: source,
            cls.JOB: job,
            cls.FILES: files,
            cls.CHECKPOINTS: checkpoints,
            cls.LIVE: live,
        }

    @classmethod
    def resume(cls, job, files, live, configuration):
        return {
            cls.JOB: job,
            cls.FILES: files,
            cls.LIVE: live,
            JobRecord.Field.CONFIGURATION: configuration,
        }

    @classmethod
    def publication(cls, source):
        return {cls.SOURCE: source}


class JobCleanupRow:
    JOB_ID = "job_id"
    STATUS = JobRecord.Field.STATUS
    LAST_ACTIVITY = "last_activity"
    BYTES = CheckpointPlan.bytes.__name__
    REVISION = CheckpointPlan.revision.__name__
    REASONS = "reasons"

    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[self.JOB_ID]

    @property
    def last_activity(self):
        return self._value[self.LAST_ACTIVITY]

    @classmethod
    def create(cls, identifier, status, ended, size, revision):
        return {
            cls.JOB_ID: identifier,
            cls.STATUS: status,
            cls.LAST_ACTIVITY: ended,
            cls.BYTES: size,
            cls.REVISION: revision,
        }

    @classmethod
    def protected(cls, row, reasons):
        return {**row, cls.REASONS: reasons}


class JobCleanupPlan:
    PROJECT_ID = ResourceRef.project_id.__name__
    COMPONENT = ResourceRef.component.__name__
    PUBLICATION = "publication"
    INVENTORY = "inventory"
    CANDIDATES = "candidates"
    PROTECTED = "protected"
    VERSION = "version"

    def __init__(self, value):
        self._value = value

    @property
    def candidates(self):
        return self._value[self.CANDIDATES]

    @property
    def version(self):
        return self._value[self.VERSION]

    @classmethod
    def create(
        cls, project_id, component, publication, inventory, candidates, protected
    ):
        return {
            cls.PROJECT_ID: project_id,
            cls.COMPONENT: component,
            cls.PUBLICATION: publication,
            cls.INVENTORY: inventory,
            cls.CANDIDATES: candidates,
            cls.PROTECTED: protected,
        }

    @classmethod
    def versioned(cls, value):
        return {**value, cls.VERSION: revision_token(value)}


class JobObservation:
    """Borrowed current public Job facts; JobRecord remains the storage validator."""

    EMPTY_ERROR = ""

    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[JobRecord.Field.ID]

    @property
    def status(self):
        return self._value[JobRecord.Field.STATUS]

    @property
    def phase(self):
        return self._value[JobRecord.Field.PHASE]

    @property
    def resume_abandonment(self):
        return self._value[JobRecord.Field.RESUME_ABANDONMENT]

    @property
    def cancel_requested(self):
        return self._value.get(JobRecord.Field.CANCEL_REQUESTED)

    @property
    def started_at(self):
        return self._value.get(JobRecord.Field.STARTED_AT)

    @property
    def ended_at(self):
        return self._value.get(JobRecord.Field.ENDED_AT)

    @property
    def created_at(self):
        return self._value[JobRecord.Field.CREATED_AT]

    @property
    def diagnostic(self):
        return self._value.get(JobRecord.Field.DIAGNOSTIC)

    @property
    def error(self):
        return self._value.get(JobRecord.Field.ERROR, self.EMPTY_ERROR)

    @property
    def ingestion(self):
        return self._value.get(JobRecord.Field.INGESTION, {})

    @property
    def progress(self):
        return self._value.get(JobRecord.Field.PROGRESS, {})

    @property
    def failures(self):
        return self._value.get(JobRecord.Field.FAILURES, [])

    @property
    def document_ids(self):
        return (
            self._value[JobRecord.Field.DOCUMENT_IDS]
            if JobRecord.Field.DOCUMENT_IDS in self._value
            else [self._value[JobRecord.Field.DOCUMENT_ID]]
        )

    @property
    def required_started_at(self):
        return self._value[JobRecord.Field.STARTED_AT]

    @property
    def required_ended_at(self):
        return self._value[JobRecord.Field.ENDED_AT]

    @classmethod
    def live(cls, job, status, phase):
        return {**job, JobRecord.Field.STATUS: status, JobRecord.Field.PHASE: phase}


class ProgressCounts:
    COMPLETED = OperationProgress.completed.__name__
    TOTAL = OperationProgress.total.__name__
    UNIT = OperationProgress.unit.__name__
    ZERO = 0

    def __init__(self, value):
        self._value = value

    @property
    def completed(self):
        return self._value.get(self.COMPLETED, self.ZERO)

    @property
    def optional_completed(self):
        return self._value.get(self.COMPLETED)

    @property
    def optional_total(self):
        return self._value.get(self.TOTAL)

    @property
    def total(self):
        return self._value[self.TOTAL]

    @property
    def optional_unit(self):
        return self._value.get(self.UNIT)

    @classmethod
    def create(cls, completed, total, unit):
        return {cls.COMPLETED: completed, cls.TOTAL: total, cls.UNIT: unit}

    @classmethod
    def observe(cls, progress):
        return cls.create(progress.completed, progress.total, progress.unit)
