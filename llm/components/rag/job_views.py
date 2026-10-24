"""RAG Job planning projections; observing a plan never grants execution."""
from llm.core.contracts import ResourceRef
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
        return {cls.SOURCE: source, cls.JOB: job, cls.FILES: files, cls.CHECKPOINTS: checkpoints, cls.LIVE: live}

    @classmethod
    def resume(cls, job, files, live, configuration):
        return {cls.JOB: job, cls.FILES: files, cls.LIVE: live, JobRecord.Field.CONFIGURATION: configuration}

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

    @classmethod
    def create(cls, identifier, status, ended, size, revision):
        return {cls.JOB_ID: identifier, cls.STATUS: status, cls.LAST_ACTIVITY: ended,
                cls.BYTES: size, cls.REVISION: revision}

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

    @classmethod
    def create(cls, project_id, component, publication, inventory, candidates, protected):
        return {cls.PROJECT_ID: project_id, cls.COMPONENT: component, cls.PUBLICATION: publication,
                cls.INVENTORY: inventory, cls.CANDIDATES: candidates, cls.PROTECTED: protected}

    @classmethod
    def versioned(cls, value):
        return {**value, cls.VERSION: revision_token(value)}

