"""RAG publication evidence and catalog projections, without filesystem effects."""

import re
from llm.components.base import validate_name
from llm.services.infrastructure.storage import revision_token
from .corpus_records import RAGCorpus
from .document_records import RAGDocument


class CorpusIdentity:
    ID = "id"
    INVALID = "RAG identity record is incomplete or changed"

    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[self.ID]

    @classmethod
    def create(cls, identifier):
        return {cls.ID: identifier}

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or value.keys() != {cls.ID}:
            raise ValueError(cls.INVALID)


class CorpusPointer:
    INVALID = "RAG active pointer is incomplete or changed"

    def __init__(self, value):
        self._value = value

    @property
    def generation(self):
        return self._value[RAGCorpus.Field.GENERATION]

    @classmethod
    def create(cls, generation):
        return {RAGCorpus.Field.GENERATION: generation}

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or value.keys() != {RAGCorpus.Field.GENERATION}:
            raise ValueError(cls.INVALID)


class CorpusControl:
    SCHEMA_VERSION = 1
    ABSENT = None
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID_FIELDS = "RAG corpus manifest is incomplete or changed"
    INVALID_VERSION = "Unsupported RAG corpus manifest version"
    WRONG_OWNER = "RAG corpus manifest ownership mismatch"
    CHANGED_POINTER = "RAG identity or active pointer integrity mismatch"
    UNEXPECTED_PUBLICATION = "Empty RAG storage cannot declare published evidence"
    MISSING_REVISIONS = "RAG publication requires its corpus and catalog revisions"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        IDENTITY_REVISION = "identity_revision"
        ACTIVE_REVISION = "active_revision"
        CORPUS_REVISION = "corpus_revision"
        CATALOG_REVISION = "catalog_revision"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT,
                       Field.IDENTITY_REVISION, Field.ACTIVE_REVISION,
                       Field.CORPUS_REVISION, Field.CATALOG_REVISION))
    PUBLICATION_REVISIONS = (Field.CORPUS_REVISION, Field.CATALOG_REVISION)

    def __init__(self, value):
        self._value = value

    @property
    def corpus_revision(self):
        return self._value[self.Field.CORPUS_REVISION]

    @property
    def catalog_revision(self):
        return self._value[self.Field.CATALOG_REVISION]

    @classmethod
    def create(cls, project_id, component, identity, generation, corpus, catalog):
        return {cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION,
                cls.Field.PROJECT_ID: project_id, cls.Field.COMPONENT: component,
                cls.Field.IDENTITY_REVISION: revision_token(CorpusIdentity.create(identity)),
                cls.Field.ACTIVE_REVISION: revision_token(CorpusPointer.create(generation)),
                cls.Field.CORPUS_REVISION: cls.ABSENT if corpus is cls.ABSENT else revision_token(corpus),
                cls.Field.CATALOG_REVISION: cls.ABSENT if catalog is cls.ABSENT else revision_token(catalog)}

    @classmethod
    def validate(cls, control, project_id, component, identity, active):
        if not isinstance(control, dict) or control.keys() != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if type(control[cls.Field.SCHEMA_VERSION]) is not int or control[cls.Field.SCHEMA_VERSION] != cls.SCHEMA_VERSION:
            raise ValueError(cls.INVALID_VERSION)
        if control[cls.Field.PROJECT_ID] != project_id or control[cls.Field.COMPONENT] != component:
            raise ValueError(cls.WRONG_OWNER)
        if (control[cls.Field.IDENTITY_REVISION] != revision_token(identity)
                or control[cls.Field.ACTIVE_REVISION] != revision_token(active)):
            raise ValueError(cls.CHANGED_POINTER)

    @classmethod
    def validate_revisions(cls, control, generation):
        for name in cls.PUBLICATION_REVISIONS:
            value = control[name]
            if generation is cls.ABSENT:
                if value is not cls.ABSENT:
                    raise ValueError(cls.UNEXPECTED_PUBLICATION)
            elif not isinstance(value, str) or re.fullmatch(cls.DIGEST_PATTERN, value) is cls.ABSENT:
                raise ValueError(cls.MISSING_REVISIONS)


class CorpusCatalog:
    REQUIRED = frozenset((RAGDocument.Field.ID, RAGDocument.Field.TITLE,
                          RAGDocument.Field.GENERATION, RAGDocument.Field.REVISION))
    INVALID_LIST = "RAG catalog must be a list"
    INVALID_FIELDS = "RAG catalog requires complete current document fields"
    INVALID_TITLE = "RAG catalog title must be non-empty text"
    INVALID_DESCRIPTION = "RAG catalog description must be text"
    INVALID_ORDER = "RAG catalog identifiers must be unique and sorted"
    CHANGED = "RAG catalog integrity mismatch"

    @classmethod
    def rows(cls, documents):
        return [{key: doc[key] for key in (*sorted(cls.REQUIRED), RAGDocument.Field.DESCRIPTION) if key in doc}
                for _, doc in sorted(documents.items())]

    @classmethod
    def validate(cls, rows, control):
        if not isinstance(rows, list):
            raise ValueError(cls.INVALID_LIST)
        identifiers = []
        for row in rows:
            if (not isinstance(row, dict) or not cls.REQUIRED <= row.keys()
                    or row.keys() - cls.REQUIRED - {RAGDocument.Field.DESCRIPTION}):
                raise ValueError(cls.INVALID_FIELDS)
            identifier = validate_name(RAGDocument(row).identifier)
            RAGDocument.validate_versions({identifier: row})
            if not isinstance(RAGDocument(row).title, str) or not RAGDocument(row).title.strip():
                raise ValueError(cls.INVALID_TITLE)
            if RAGDocument.Field.DESCRIPTION in row and not isinstance(row[RAGDocument.Field.DESCRIPTION], str):
                raise ValueError(cls.INVALID_DESCRIPTION)
            identifiers.append(identifier)
        if identifiers != sorted(set(identifiers)):
            raise ValueError(cls.INVALID_ORDER)
        if revision_token(rows) != CorpusControl(control).catalog_revision:
            raise ValueError(cls.CHANGED)
        return rows
