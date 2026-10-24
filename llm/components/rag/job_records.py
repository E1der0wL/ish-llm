"""RAG-owned Job state, attempt and failure evidence; readers never repair omissions."""

from datetime import datetime
from dataclasses import fields
import re
from llm.components.base import validate_name
from llm.core.checkpoints import ResumeAbandonment
from llm.core.contracts import DiagnosticObservation, Diagnostic, OperationProgress, ResourceRef
from .status import RAGJobStatus


class JobProgress:
    COMPLETE = 1
    UNIT = "job"
    PREPARING = "preparing"
    PREPARING_DOCUMENTS = "preparing_documents"
    DOCUMENT_UNIT = "document"
    DOCUMENTS_COMPLETED = "documents_completed"
    DOCUMENTS_TOTAL = "documents_total"
    DOCUMENT_INDEX = "document_index"
    COUNT_FIELDS = (OperationProgress.completed.__name__, OperationProgress.total.__name__, OperationProgress.unit.__name__)
    DEFAULT_MESSAGE = OperationProgress.__dataclass_fields__[OperationProgress.message.__name__].default

    @classmethod
    def document(cls, position, total):
        return {OperationProgress.phase.__name__: cls.PREPARING_DOCUMENTS,
                OperationProgress.completed.__name__: position, OperationProgress.total.__name__: total,
                OperationProgress.unit.__name__: cls.DOCUMENT_UNIT, cls.DOCUMENTS_COMPLETED: position,
                cls.DOCUMENTS_TOTAL: total, cls.DOCUMENT_INDEX: position}

    @classmethod
    def documents_finished(cls, count):
        return {OperationProgress.phase.__name__: cls.PREPARING_DOCUMENTS,
                OperationProgress.completed.__name__: count, OperationProgress.total.__name__: count,
                OperationProgress.unit.__name__: cls.DOCUMENT_UNIT,
                cls.DOCUMENTS_COMPLETED: count, cls.DOCUMENTS_TOTAL: count}

    @classmethod
    def observe(cls, job, values):
        if JobRecord.Field.PHASE in values:
            job[JobRecord.Field.PHASE] = values[JobRecord.Field.PHASE]
            job[JobRecord.Field.PROGRESS] = {key: values[key] for key in cls.COUNT_FIELDS if key in values}
        job[JobRecord.Field.INGESTION] = {**job[JobRecord.Field.INGESTION], **{key: value for key, value in values.items()
                           if key not in (JobRecord.Field.PHASE, *cls.COUNT_FIELDS)}}


    @classmethod
    def completed(cls):
        return {OperationProgress.completed.__name__: cls.COMPLETE,
                OperationProgress.total.__name__: cls.COMPLETE, OperationProgress.unit.__name__: cls.UNIT}


class JobRecord:
    ERROR_TIME_REQUIRES_TIMEZONE_AWARE_ISO_TEXT = 'RAG Job time requires timezone-aware ISO text'
    ERROR_INVALID_FAILURE_EVIDENCE = 'Invalid RAG Job failure evidence'
    ERROR_DIAGNOSTIC_REQUIRES_ITS_COMPLETE_SOURCE = 'RAG Job diagnostic requires its complete source'
    ERROR_DIAGNOSTIC_DISAGREES_WITH_ITS_FAILURE = 'RAG Job diagnostic disagrees with its failure'
    ERROR_INVALID_STATE_FIELDS = 'Invalid RAG Job state fields'
    ERROR_UNSTARTED_HAS_ATTEMPT_OBSERVATIONS = 'Unstarted RAG Job has attempt observations'
    ERROR_QUEUED_HAS_EXECUTION_OR_CANCELLATION_EVIDENCE = 'Queued RAG Job has execution or cancellation evidence'
    ERROR_UNSTARTED_CANCELLED_REQUIRES_ITS_CANCELLATION = 'Unstarted cancelled RAG Job requires its cancellation'
    ERROR_COMPLETED_REQUIRES_ITS_PUBLICATION_PROGRESS = 'Completed RAG Job requires its publication progress'
    ERROR_INVALID_RECORD = 'Invalid RAG Job record'
    ERROR_REQUIRES_EXACTLY_ONE_DOCUMENT_DECLARATION = 'RAG Job requires exactly one document declaration'
    ERROR_CREATION_EVIDENCE_CANNOT_CHANGE = 'RAG Job creation evidence cannot change'
    ERROR_INVALID_STATE_TRANSITION = 'Invalid RAG Job state transition'
    ERROR_CANCELLATION_CANNOT_BE_REVOKED = 'RAG Job cancellation cannot be revoked'
    ERROR_ABANDONMENT_EVIDENCE_CANNOT_CHANGE = 'RAG Job abandonment evidence cannot change'
    ERROR_INVALID_PROGRESS = 'Invalid RAG Job progress'
    ERROR_ATTEMPT_EVIDENCE_IS_INCOMPLETE = 'RAG Job attempt evidence is incomplete'
    ERROR_INVALID_WORKER_OR_CONFIGURATION_EVIDENCE = 'Invalid RAG Job worker or configuration evidence'
    ERROR_REQUIRES_ITS_STORED_ATTEMPT_EVIDENCE = 'RAG Job requires its stored attempt evidence'
    ERROR_REQUIRES_ITS_STORED_FAILURE_EVIDENCE = 'RAG Job requires its stored failure evidence'
    ERROR_HAS_FAILURE_EVIDENCE_OUTSIDE_A_FAILED_ATTEMPT = 'RAG Job has failure evidence outside a failed attempt'
    ERROR_REQUIRES_ITS_STORED_COMPLETION_TIME = 'RAG Job requires its stored completion time'
    ERROR_ENDED_BEFORE_A_TERMINAL_ATTEMPT = 'RAG Job ended before a terminal attempt'
    ERROR_RUNNING_REQUIRES_AN_ACTIVE_PHASE = 'Running RAG Job requires an active phase'
    ERROR_STATUS_AND_PHASE_DISAGREE = 'RAG Job status and phase disagree'
    ERROR_INVALID_FAILURE_HISTORY = 'Invalid RAG Job failure history'
    ERROR_DOCUMENT_IDS_MUST_BE_A_NONEMPTY_LIST = 'RAG Job document IDs must be a nonempty list'
    ERROR_DOCUMENT_IDS_MUST_BE_UNIQUE = 'RAG Job document IDs must be unique'
    ERROR_ABANDONMENT_REQUIRES_ITS_COMPLETE_RECEIPT = 'RAG Job abandonment requires its complete receipt'

    ABSENT = None
    MINIMUM_PID = 1
    NOT_CANCELLED = False
    CANCEL_REQUESTED = True
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    KIND = "rag_job"

    class Field:
        ID = 'id'
        CREATED_AT = 'created_at'
        INPUT_DIGEST = 'input_digest'
        STATUS = 'status'
        PHASE = OperationProgress.phase.__name__
        CANCEL_REQUESTED = 'cancel_requested'
        RESUME_ABANDONMENT = ResumeAbandonment.KEY
        FAILURES = 'failures'
        PROGRESS = 'progress'
        INGESTION = 'ingestion'
        STARTED_AT = 'started_at'
        PID = 'pid'
        CONFIGURATION = 'configuration'
        FAILED_PHASE = 'failed_phase'
        ERROR = 'error'
        DIAGNOSTIC_CODE = 'diagnostic_code'
        DIAGNOSTIC = DiagnosticObservation.ATTRIBUTE
        ENDED_AT = 'ended_at'
        DOCUMENT_ID = 'document_id'
        DOCUMENT_IDS = 'document_ids'

    STATUSES = frozenset(status.value for status in RAGJobStatus)
    TRANSITIONS = {RAGJobStatus.QUEUED.value: (RAGJobStatus.QUEUED.value, RAGJobStatus.RUNNING.value, RAGJobStatus.CANCELLED.value), RAGJobStatus.RUNNING.value: (RAGJobStatus.RUNNING.value, RAGJobStatus.COMPLETED.value, RAGJobStatus.FAILED.value, RAGJobStatus.CANCELLED.value),
                   RAGJobStatus.FAILED.value: (RAGJobStatus.FAILED.value, RAGJobStatus.RUNNING.value, RAGJobStatus.CANCELLED.value), RAGJobStatus.COMPLETED.value: (RAGJobStatus.COMPLETED.value,), RAGJobStatus.CANCELLED.value: (RAGJobStatus.CANCELLED.value,)}
    CREATION_FIELDS = frozenset((Field.ID, Field.CREATED_AT, Field.INPUT_DIGEST))
    REQUIRED = CREATION_FIELDS | {Field.STATUS, Field.PHASE, Field.CANCEL_REQUESTED, Field.RESUME_ABANDONMENT, Field.FAILURES, Field.PROGRESS, Field.INGESTION}
    ATTEMPT_FIELDS = frozenset((Field.STARTED_AT, Field.PID, Field.CONFIGURATION))
    ERROR_FIELDS = frozenset((Field.FAILED_PHASE, Field.ERROR, Field.DIAGNOSTIC_CODE, Field.DIAGNOSTIC))
    FAILURE_FIELDS = ERROR_FIELDS | {Field.STATUS, Field.STARTED_AT, Field.ENDED_AT}
    FIELDS = REQUIRED | ATTEMPT_FIELDS | ERROR_FIELDS | {Field.ENDED_AT, Field.DOCUMENT_ID, Field.DOCUMENT_IDS, Field.RESUME_ABANDONMENT}

    @classmethod
    def queued(cls, identifier, documents, created_at, input_digest):
        return {cls.Field.ID: identifier, **documents, cls.Field.STATUS: RAGJobStatus.QUEUED.value,
                cls.Field.CREATED_AT: created_at, cls.Field.INPUT_DIGEST: input_digest,
                cls.Field.PHASE: RAGJobStatus.QUEUED.value, cls.Field.CANCEL_REQUESTED: cls.NOT_CANCELLED,
                cls.Field.RESUME_ABANDONMENT: cls.ABSENT, cls.Field.FAILURES: [],
                cls.Field.PROGRESS: {}, cls.Field.INGESTION: {}}

    @staticmethod
    def _time(value):
        if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is JobRecord.ABSENT:
            raise ValueError(JobRecord.ERROR_TIME_REQUIRES_TIMEZONE_AWARE_ISO_TEXT)

    @staticmethod
    def _diagnostic(value):
        if (not isinstance(value[JobRecord.Field.FAILED_PHASE], str) or not value[JobRecord.Field.FAILED_PHASE]
                or not isinstance(value[JobRecord.Field.DIAGNOSTIC], dict)
                or value[JobRecord.Field.DIAGNOSTIC].keys() != {field.name for field in fields(Diagnostic)}):
            raise ValueError(JobRecord.ERROR_INVALID_FAILURE_EVIDENCE)
        source = value[JobRecord.Field.DIAGNOSTIC][Diagnostic.source.__name__]
        if not isinstance(source, dict) or source.keys() != {field.name for field in fields(ResourceRef)}:
            raise ValueError(JobRecord.ERROR_DIAGNOSTIC_REQUIRES_ITS_COMPLETE_SOURCE)
        diagnostic = Diagnostic.from_dict(value[JobRecord.Field.DIAGNOSTIC])
        if (diagnostic.code != value[JobRecord.Field.ERROR] or diagnostic.code != value[JobRecord.Field.DIAGNOSTIC_CODE]
                or diagnostic.source is JobRecord.ABSENT):
            raise ValueError(JobRecord.ERROR_DIAGNOSTIC_DISAGREES_WITH_ITS_FAILURE)

    @classmethod
    def _state(cls, job, identifier):
        status, present = job[JobRecord.Field.STATUS], job.keys()
        if (present - cls.FIELDS or type(job[JobRecord.Field.CANCEL_REQUESTED]) is not bool
                or not isinstance(job[JobRecord.Field.FAILURES], list) or not isinstance(job[JobRecord.Field.PROGRESS], dict)
                or not isinstance(job[JobRecord.Field.INGESTION], dict)):
            raise ValueError(JobRecord.ERROR_INVALID_STATE_FIELDS)
        try:
            OperationProgress(ResourceRef(cls.KIND, identifier), job[JobRecord.Field.PHASE], **job[JobRecord.Field.PROGRESS])
        except TypeError as error:
            raise ValueError(JobRecord.ERROR_INVALID_PROGRESS) from error
        attempted = bool(present & cls.ATTEMPT_FIELDS)
        if attempted:
            if not cls.ATTEMPT_FIELDS <= present:
                raise ValueError(JobRecord.ERROR_ATTEMPT_EVIDENCE_IS_INCOMPLETE)
            cls._time(job[JobRecord.Field.STARTED_AT])
            if (type(job[JobRecord.Field.PID]) is not int or job[JobRecord.Field.PID] < JobRecord.MINIMUM_PID
                    or not isinstance(job[JobRecord.Field.CONFIGURATION], str)
                    or re.fullmatch(JobRecord.DIGEST_PATTERN, job[JobRecord.Field.CONFIGURATION]) is JobRecord.ABSENT):
                raise ValueError(JobRecord.ERROR_INVALID_WORKER_OR_CONFIGURATION_EVIDENCE)
        elif status in (RAGJobStatus.RUNNING.value, RAGJobStatus.COMPLETED.value, RAGJobStatus.FAILED.value):
            raise ValueError(JobRecord.ERROR_REQUIRES_ITS_STORED_ATTEMPT_EVIDENCE)
        terminal_failure = status == RAGJobStatus.FAILED.value or (status == RAGJobStatus.CANCELLED.value and attempted)
        if terminal_failure:
            if not cls.ERROR_FIELDS <= present or JobRecord.Field.ENDED_AT not in job:
                raise ValueError(JobRecord.ERROR_REQUIRES_ITS_STORED_FAILURE_EVIDENCE)
            cls._diagnostic(job)
        elif present & cls.ERROR_FIELDS:
            raise ValueError(JobRecord.ERROR_HAS_FAILURE_EVIDENCE_OUTSIDE_A_FAILED_ATTEMPT)
        if status == RAGJobStatus.COMPLETED.value or terminal_failure:
            if JobRecord.Field.ENDED_AT not in job:
                raise ValueError(JobRecord.ERROR_REQUIRES_ITS_STORED_COMPLETION_TIME)
            cls._time(job[JobRecord.Field.ENDED_AT])
        elif JobRecord.Field.ENDED_AT in job:
            raise ValueError(JobRecord.ERROR_ENDED_BEFORE_A_TERMINAL_ATTEMPT)
        if status == RAGJobStatus.RUNNING.value:
            if job[JobRecord.Field.PHASE] in cls.STATUSES:
                raise ValueError(JobRecord.ERROR_RUNNING_REQUIRES_AN_ACTIVE_PHASE)
        elif job[JobRecord.Field.PHASE] != status:
            raise ValueError(JobRecord.ERROR_STATUS_AND_PHASE_DISAGREE)
        if not attempted and (job[JobRecord.Field.FAILURES] or job[JobRecord.Field.PROGRESS] or job[JobRecord.Field.INGESTION]):
            raise ValueError(JobRecord.ERROR_UNSTARTED_HAS_ATTEMPT_OBSERVATIONS)
        if status == RAGJobStatus.QUEUED.value and (attempted or job[JobRecord.Field.CANCEL_REQUESTED]):
            raise ValueError(JobRecord.ERROR_QUEUED_HAS_EXECUTION_OR_CANCELLATION_EVIDENCE)
        if status == RAGJobStatus.CANCELLED.value and not attempted and not job[JobRecord.Field.CANCEL_REQUESTED]:
            raise ValueError(JobRecord.ERROR_UNSTARTED_CANCELLED_REQUIRES_ITS_CANCELLATION)
        if status == RAGJobStatus.COMPLETED.value and (job[JobRecord.Field.CANCEL_REQUESTED] or
                job[JobRecord.Field.PROGRESS] != JobProgress.completed()):
            raise ValueError(JobRecord.ERROR_COMPLETED_REQUIRES_ITS_PUBLICATION_PROGRESS)
        for failure in job[JobRecord.Field.FAILURES]:
            if (not isinstance(failure, dict) or failure.keys() != cls.FAILURE_FIELDS
                    or failure[JobRecord.Field.STATUS] != RAGJobStatus.FAILED.value):
                raise ValueError(JobRecord.ERROR_INVALID_FAILURE_HISTORY)
            cls._time(failure[JobRecord.Field.STARTED_AT])
            cls._time(failure[JobRecord.Field.ENDED_AT])
            cls._diagnostic(failure)

    @classmethod
    def validate(cls, job, identifier):
        if (not isinstance(job, dict) or not cls.REQUIRED <= job.keys()
                or job[JobRecord.Field.ID] != identifier or not isinstance(job[JobRecord.Field.STATUS], str)
                or job[JobRecord.Field.STATUS] not in cls.STATUSES
                or not isinstance(job[JobRecord.Field.PHASE], str) or not job[JobRecord.Field.PHASE]
                or not isinstance(job[JobRecord.Field.INPUT_DIGEST], str)
                or re.fullmatch(JobRecord.DIGEST_PATTERN, job[JobRecord.Field.INPUT_DIGEST]) is JobRecord.ABSENT):
            raise ValueError(JobRecord.ERROR_INVALID_RECORD)
        cls._time(job[JobRecord.Field.CREATED_AT])
        cls._state(job, identifier)
        if (JobRecord.Field.DOCUMENT_ID in job) == (JobRecord.Field.DOCUMENT_IDS in job):
            raise ValueError(JobRecord.ERROR_REQUIRES_EXACTLY_ONE_DOCUMENT_DECLARATION)
        if JobRecord.Field.DOCUMENT_ID in job:
            validate_name(job[JobRecord.Field.DOCUMENT_ID])
        else:
            identifiers = job[JobRecord.Field.DOCUMENT_IDS]
            if not isinstance(identifiers, list) or not identifiers:
                raise ValueError(JobRecord.ERROR_DOCUMENT_IDS_MUST_BE_A_NONEMPTY_LIST)
            for value in identifiers:
                validate_name(value)
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(JobRecord.ERROR_DOCUMENT_IDS_MUST_BE_UNIQUE)
        if job[JobRecord.Field.RESUME_ABANDONMENT] is not JobRecord.ABSENT:
            if (not isinstance(job[JobRecord.Field.RESUME_ABANDONMENT], dict)
                    or job[JobRecord.Field.RESUME_ABANDONMENT].keys() != {field.name for field in fields(ResumeAbandonment)}):
                raise ValueError(JobRecord.ERROR_ABANDONMENT_REQUIRES_ITS_COMPLETE_RECEIPT)
            ResumeAbandonment.from_dict(job[JobRecord.Field.RESUME_ABANDONMENT])
        return job

    @classmethod
    def require_update(cls, job, previous):
        immutable = cls.CREATION_FIELDS
        immutable |= {key for key in (JobRecord.Field.DOCUMENT_ID, JobRecord.Field.DOCUMENT_IDS) if key in previous}
        if any(job.get(key) != previous[key] for key in immutable):
            raise ValueError(JobRecord.ERROR_CREATION_EVIDENCE_CANNOT_CHANGE)
        if job[JobRecord.Field.STATUS] not in cls.TRANSITIONS[previous[JobRecord.Field.STATUS]]:
            raise ValueError(JobRecord.ERROR_INVALID_STATE_TRANSITION)
        if previous[JobRecord.Field.CANCEL_REQUESTED] and not job[JobRecord.Field.CANCEL_REQUESTED]:
            raise ValueError(JobRecord.ERROR_CANCELLATION_CANNOT_BE_REVOKED)
        if previous[JobRecord.Field.RESUME_ABANDONMENT] is not JobRecord.ABSENT and job[JobRecord.Field.RESUME_ABANDONMENT] != previous[JobRecord.Field.RESUME_ABANDONMENT]:
            raise ValueError(JobRecord.ERROR_ABANDONMENT_EVIDENCE_CANNOT_CHANGE)


    @classmethod
    def request_cancellation(cls, job):
        job[cls.Field.CANCEL_REQUESTED] = cls.CANCEL_REQUESTED
        if job[cls.Field.STATUS] != RAGJobStatus.RUNNING.value:
            job[cls.Field.STATUS] = RAGJobStatus.CANCELLED.value
            job[cls.Field.PHASE] = RAGJobStatus.CANCELLED.value

    @classmethod
    def archive_attempt(cls, job):
        if cls.Field.DIAGNOSTIC in job:
            job[cls.Field.FAILURES].append({key: job[key] for key in cls.FAILURE_FIELDS})
        for key in cls.ERROR_FIELDS | {cls.Field.ENDED_AT}:
            job.pop(key, cls.ABSENT)

    @classmethod
    def start_attempt(cls, job, pid, started_at, configuration):
        job.update({cls.Field.STATUS: RAGJobStatus.RUNNING.value, cls.Field.PHASE: JobProgress.PREPARING,
                    cls.Field.PROGRESS: {}, cls.Field.PID: pid, cls.Field.STARTED_AT: started_at,
                    cls.Field.CONFIGURATION: configuration})

    @classmethod
    def finish_publication(cls, job, ended_at):
        job.update({cls.Field.STATUS: RAGJobStatus.COMPLETED.value, cls.Field.PHASE: RAGJobStatus.COMPLETED.value,
                    cls.Field.PROGRESS: JobProgress.completed(), cls.Field.ENDED_AT: ended_at})

    @classmethod
    def finish_failure(cls, job, cancelled, code, diagnostic, ended_at):
        job.update({cls.Field.STATUS: RAGJobStatus.CANCELLED.value if cancelled else RAGJobStatus.FAILED.value,
                    cls.Field.PHASE: RAGJobStatus.CANCELLED.value if cancelled else RAGJobStatus.FAILED.value,
                    cls.Field.ERROR: code, cls.Field.DIAGNOSTIC_CODE: code,
                    cls.Field.DIAGNOSTIC: diagnostic, cls.Field.ENDED_AT: ended_at})


class JobFailure:
    CANCELLED = "rag_cancelled"
    FAILED = "rag_failed"
    BOUNDARY = "component"
    OBSERVED_CODE = "observed_code"
