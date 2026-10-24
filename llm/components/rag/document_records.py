"""Canonical RAG original input and prepared-document evidence, without recovery defaults."""
from copy import deepcopy
from dataclasses import fields
from uuid import uuid4
import re

from llm.components.base import validate_name
from llm.core.json_data import JsonObject
from llm.core.contracts import Diagnostic, ResourceRef
from llm.services.infrastructure.storage import revision_token
from .chunk_records import RAGContent, RAGChunk, RAGSection, SplitDocument
from .identity import RAGEvidenceIdentity
from .ingestion_records import EmbeddingEvidence, EmbeddingProfile
from .graph_records import GraphRecord, GraphValidationError, validate_stored_graph, group_evidence, select_graph


class ExtractionManifestEntry:
    FIRST_OFFSET = 0
    MINIMUM_COUNT = 1

    class Field:
        SIGNATURE = "signature"
        ENTITY_IDS = "entity_ids"
        OFFSET = "offset"
        COUNT = "count"
        DIGEST = "digest"

    FIELDS = frozenset((Field.SIGNATURE, Field.ENTITY_IDS, Field.OFFSET, Field.COUNT, Field.DIGEST))
    DIGEST_FIELDS = (Field.SIGNATURE, Field.DIGEST)

    @classmethod
    def create(cls, signature, entity_ids, offset, count):
        return {cls.Field.SIGNATURE: signature, cls.Field.ENTITY_IDS: entity_ids,
                cls.Field.OFFSET: offset, cls.Field.COUNT: count}


class RAGDocument:
    ERROR_STORED_RAG_DOCUMENT_MUST_BE_AN_OBJECT = 'Stored RAG document must be an object'
    ERROR_RAG_DOCUMENTS_REQUIRE_GRAPH_ENTITIES_AND_RELATIONS = 'RAG documents require graph entities and relations'
    ERROR_PREPARED_RAG_DOCUMENT_FIELDS_ARE_INCOMPLETE_OR_UNKNOWN = 'Prepared RAG document fields are incomplete or unknown'
    ERROR_INVALID_PREPARED_RAG_DOCUMENT_SOURCE_OR_OBSERVATIONS = 'Invalid prepared RAG document source or observations'
    ERROR_PREPARED_RAG_CONTENT_DIGEST_CHANGED = 'Prepared RAG content digest changed'
    ERROR_PREPARED_RAG_DOCUMENT_REQUIRES_SECTIONS_AND_CHUNKS = 'Prepared RAG document requires sections and chunks'
    ERROR_INVALID_PREPARED_RAG_SECTION_IDENTITIES = 'Invalid prepared RAG section identities'
    ERROR_PREPARED_RAG_CHUNK_VECTOR_COUNT_MISMATCH = 'Prepared RAG chunk/vector count mismatch'
    ERROR_INVALID_PREPARED_RAG_GRAPH_OBSERVATIONS = 'Invalid prepared RAG graph observations'
    ERROR_INVALID_PREPARED_RAG_EXTRACTION_MANIFEST = 'Invalid prepared RAG extraction manifest'
    ERROR_PREPARED_RAG_COMPLETE_GRAPH_HAS_MISSING_EXTRACTION_BATCHES = 'Prepared RAG complete graph has missing extraction batches'
    ERROR_DOCUMENT_CAS_REQUIRES_A_POSITIVE_EXPECTED_REVISION = 'Document CAS requires a positive expected_revision'
    ERROR_DOCUMENT_CAS_REQUIRES_EXPECTED_GENERATION_FROM_THE_DOCUMENT = 'Document CAS requires expected_generation from the document'
    ERROR_DOCUMENT_GENERATION_OR_REVISION_CHANGED = 'Document generation or revision changed'
    ERROR_INVALID_PREPARED_RAG_CONFIGURATION_EVIDENCE = 'Invalid prepared RAG configuration evidence'
    ERROR_INCOMPLETE_PREPARED_RAG_GRAPH_DIAGNOSTIC = 'Incomplete prepared RAG graph diagnostic'
    ERROR_INCOMPLETE_PREPARED_RAG_GRAPH_DIAGNOSTIC_SOURCE = 'Incomplete prepared RAG graph diagnostic source'
    ERROR_INVALID_PREPARED_RAG_EXTRACTION_BATCH = 'Invalid prepared RAG extraction batch'
    ERROR_OVERLAPPING_PREPARED_RAG_EXTRACTION_BATCHES = 'Overlapping prepared RAG extraction batches'
    ERROR_STORED_EXTRACTION_BATCH_INTEGRITY_MISMATCH = 'Stored extraction batch integrity mismatch'
    ERROR_RAG_DOCUMENTS_REQUIRE_A_STORED_GENERATION_AND_POSITIVE_REVISION__RE_REGISTER_SOURCES_WITHOUT_THEM = 'RAG documents require a stored generation and positive revision; re-register sources without them'

    ABSENT = None
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INITIAL_REVISION = 1
    REVISION_INCREMENT = 1
    class Field:
        ID = "id"
        TITLE = "title"
        METADATA = "metadata"
        GENERATION = "generation"
        REVISION = "revision"
        DESCRIPTION = "description"
        PREPARATION_CONFIGURATION = "preparation_configuration"
        PREPARATION_FINGERPRINT = "preparation_fingerprint"
        EXTRACTION_MANIFEST = "extraction_manifest"
        GRAPH_COMPLETE = "graph_complete"
        GRAPH_DIAGNOSTICS = "graph_diagnostics"

    REQUIRED = frozenset((Field.ID, Field.TITLE, SplitDocument.Field.CONTENT, Field.METADATA, Field.REVISION, Field.GENERATION,
        SplitDocument.Field.SECTIONS, SplitDocument.Field.CHUNKS, SplitDocument.Field.SHA256, EmbeddingEvidence.Field.VECTORS, EmbeddingEvidence.Field.FINGERPRINT, EmbeddingEvidence.Field.INGESTION, EmbeddingEvidence.Field.PROFILE,
        Field.GRAPH_COMPLETE, Field.GRAPH_DIAGNOSTICS, GraphRecord.Field.GRAPH, Field.EXTRACTION_MANIFEST,
        Field.PREPARATION_CONFIGURATION, Field.PREPARATION_FINGERPRINT))

    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def title(self):
        return self._value[self.Field.TITLE]

    @property
    def metadata(self):
        return self._value[self.Field.METADATA]

    @property
    def generation(self):
        return self._value[self.Field.GENERATION]

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    @classmethod
    def validate(cls, document):
        """Validate a stored RAG document without filling fields or consulting live settings."""
        if not isinstance(document, dict):
            raise ValueError(RAGDocument.ERROR_STORED_RAG_DOCUMENT_MUST_BE_AN_OBJECT)
        identifier = validate_name(document.get(RAGDocument.Field.ID))
        cls.validate_versions({identifier: document})
        graph = document.get(GraphRecord.Field.GRAPH)
        if not isinstance(graph, dict) or not GraphRecord.FIELDS <= graph.keys():
            raise ValueError(RAGDocument.ERROR_RAG_DOCUMENTS_REQUIRE_GRAPH_ENTITIES_AND_RELATIONS)
        if (not cls.REQUIRED <= document.keys()
                or document.keys() - cls.REQUIRED - {RAGDocument.Field.DESCRIPTION}):
            raise ValueError(RAGDocument.ERROR_PREPARED_RAG_DOCUMENT_FIELDS_ARE_INCOMPLETE_OR_UNKNOWN)
        JsonObject.validate(document)
        if (not isinstance(document[RAGDocument.Field.TITLE], str) or not document[RAGDocument.Field.TITLE].strip()
                or not isinstance(document[SplitDocument.Field.CONTENT], str) or not document[SplitDocument.Field.CONTENT].strip()
                or not isinstance(document[RAGDocument.Field.METADATA], dict)
                or not isinstance(EmbeddingEvidence(document).observations, dict)
                or (RAGDocument.Field.DESCRIPTION in document and not isinstance(document[RAGDocument.Field.DESCRIPTION], str))):
            raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_DOCUMENT_SOURCE_OR_OBSERVATIONS)
        if document[SplitDocument.Field.SHA256] != RAGContent.digest(document[SplitDocument.Field.CONTENT]):
            raise ValueError(RAGDocument.ERROR_PREPARED_RAG_CONTENT_DIGEST_CHANGED)
        for key in (EmbeddingEvidence.Field.FINGERPRINT, RAGDocument.Field.PREPARATION_FINGERPRINT, RAGDocument.Field.PREPARATION_CONFIGURATION):
            value = document[key]
            if key == RAGDocument.Field.PREPARATION_CONFIGURATION and value is RAGDocument.ABSENT:
                continue  # Standalone preparation has no bound Project configuration.
            if not isinstance(value, str) or re.fullmatch(RAGDocument.DIGEST_PATTERN, value) is RAGDocument.ABSENT:
                raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_CONFIGURATION_EVIDENCE)
        sections, chunks = SplitDocument(document).sections, SplitDocument(document).chunks
        if not isinstance(sections, dict) or not sections or not isinstance(chunks, list) or not chunks:
            raise ValueError(RAGDocument.ERROR_PREPARED_RAG_DOCUMENT_REQUIRES_SECTIONS_AND_CHUNKS)
        if sections.keys() != {RAGSection.identifier_for(identifier, position) for position in range(RAGSection.FIRST_ORDINAL, len(sections) + RAGSection.FIRST_ORDINAL)}:
            raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_SECTION_IDENTITIES)
        for key, section in sections.items():
            RAGSection.validate_source(section, key, SplitDocument(document).content)
        for position, chunk in enumerate(chunks, RAGChunk.FIRST_ORDINAL):
            RAGChunk.validate_source(chunk, document_id=identifier, ordinal=position, sections=sections)
        profile = document[EmbeddingEvidence.Field.PROFILE]
        EmbeddingProfile.validate(profile)
        from llm.core.embeddings import validate_vectors
        vectors = validate_vectors(document[EmbeddingEvidence.Field.VECTORS], dimensions=EmbeddingProfile(profile).dimensions)
        if len(vectors) != len(chunks):
            raise ValueError(RAGDocument.ERROR_PREPARED_RAG_CHUNK_VECTOR_COUNT_MISMATCH)
        validate_stored_graph(document[GraphRecord.Field.GRAPH], chunks, identifier)
        if type(document[RAGDocument.Field.GRAPH_COMPLETE]) is not bool or not isinstance(document[RAGDocument.Field.GRAPH_DIAGNOSTICS], list):
            raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_GRAPH_OBSERVATIONS)
        for value in document[RAGDocument.Field.GRAPH_DIAGNOSTICS]:
            if not isinstance(value, dict) or value.keys() != {field.name for field in fields(Diagnostic)}:
                raise ValueError(RAGDocument.ERROR_INCOMPLETE_PREPARED_RAG_GRAPH_DIAGNOSTIC)
            source = value[Diagnostic.source.__name__]
            if source is not RAGDocument.ABSENT and (not isinstance(source, dict)
                    or source.keys() != {field.name for field in fields(ResourceRef)}):
                raise ValueError(RAGDocument.ERROR_INCOMPLETE_PREPARED_RAG_GRAPH_DIAGNOSTIC_SOURCE)
            Diagnostic.from_dict(value)
        manifest = document[RAGDocument.Field.EXTRACTION_MANIFEST]
        if not isinstance(manifest, list):
            raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_EXTRACTION_MANIFEST)
        grouped, covered = group_evidence(document[GraphRecord.Field.GRAPH]), set()
        for entry in manifest:
            if (not isinstance(entry, dict) or entry.keys() != ExtractionManifestEntry.FIELDS
                    or type(entry[ExtractionManifestEntry.Field.OFFSET]) is not int or type(entry[ExtractionManifestEntry.Field.COUNT]) is not int
                    or entry[ExtractionManifestEntry.Field.OFFSET] < ExtractionManifestEntry.FIRST_OFFSET or entry[ExtractionManifestEntry.Field.COUNT] < ExtractionManifestEntry.MINIMUM_COUNT
                    or entry[ExtractionManifestEntry.Field.OFFSET] + entry[ExtractionManifestEntry.Field.COUNT] > len(chunks)
                    or not isinstance(entry[ExtractionManifestEntry.Field.ENTITY_IDS], list)
                    or any(not isinstance(item, str) for item in entry[ExtractionManifestEntry.Field.ENTITY_IDS])
                    or len(set(entry[ExtractionManifestEntry.Field.ENTITY_IDS])) != len(entry[ExtractionManifestEntry.Field.ENTITY_IDS])
                    or any(not isinstance(entry[key], str) or re.fullmatch(RAGDocument.DIGEST_PATTERN, entry[key]) is RAGDocument.ABSENT
                           for key in ExtractionManifestEntry.DIGEST_FIELDS)):
                raise ValueError(RAGDocument.ERROR_INVALID_PREPARED_RAG_EXTRACTION_BATCH)
            indices = set(range(entry[ExtractionManifestEntry.Field.OFFSET], entry[ExtractionManifestEntry.Field.OFFSET] + entry[ExtractionManifestEntry.Field.COUNT]))
            if covered & indices:
                raise ValueError(RAGDocument.ERROR_OVERLAPPING_PREPARED_RAG_EXTRACTION_BATCHES)
            covered.update(indices)
            graph = select_graph(grouped, entry[ExtractionManifestEntry.Field.ENTITY_IDS], chunks[entry[ExtractionManifestEntry.Field.OFFSET]:entry[ExtractionManifestEntry.Field.OFFSET] + entry[ExtractionManifestEntry.Field.COUNT]])
            if revision_token(graph) != entry[ExtractionManifestEntry.Field.DIGEST]:
                raise GraphValidationError(RAGDocument.ERROR_STORED_EXTRACTION_BATCH_INTEGRITY_MISMATCH)
        if document[RAGDocument.Field.GRAPH_COMPLETE] and covered != set(range(len(chunks))):
            raise ValueError(RAGDocument.ERROR_PREPARED_RAG_COMPLETE_GRAPH_HAS_MISSING_EXTRACTION_BATCHES)
        return document

    @classmethod
    def validate_versions(cls, documents):
        for identifier, document in documents.items():
            if (document.get(RAGDocument.Field.ID) != identifier or type(document.get(RAGDocument.Field.REVISION)) is not int
                    or document[RAGDocument.Field.REVISION] < cls.INITIAL_REVISION or not isinstance(document.get(RAGDocument.Field.GENERATION), str)
                    or RAGEvidenceIdentity.PATTERN.fullmatch(document[RAGDocument.Field.GENERATION]) is RAGDocument.ABSENT):
                raise ValueError(RAGDocument.ERROR_RAG_DOCUMENTS_REQUIRE_A_STORED_GENERATION_AND_POSITIVE_REVISION__RE_REGISTER_SOURCES_WITHOUT_THEM)

    @classmethod
    def check_revision(cls, document, expected_revision, expected_generation, conflict):
        """명시적인 CAS는 문서 생성 세대까지 비교한다. 코퍼스 publish 세대와는 별개다."""
        if expected_revision is RAGDocument.ABSENT and expected_generation is RAGDocument.ABSENT:
            return  # 두 값 모두 생략한 기존 unconditional document API 계약.
        if type(expected_revision) is not int or expected_revision < cls.INITIAL_REVISION:
            raise ValueError(RAGDocument.ERROR_DOCUMENT_CAS_REQUIRES_A_POSITIVE_EXPECTED_REVISION)
        if not isinstance(expected_generation, str) or RAGEvidenceIdentity.PATTERN.fullmatch(expected_generation) is RAGDocument.ABSENT:
            raise ValueError(RAGDocument.ERROR_DOCUMENT_CAS_REQUIRES_EXPECTED_GENERATION_FROM_THE_DOCUMENT)
        if (document[RAGDocument.Field.GENERATION], document[RAGDocument.Field.REVISION]) != (expected_generation, expected_revision):
            raise conflict(RAGDocument.ERROR_DOCUMENT_GENERATION_OR_REVISION_CHANGED)


class RAGDocumentInput:
    ERROR_RAG_DOCUMENT_INPUT_FIELDS_ARE_INCOMPLETE_OR_UNKNOWN = 'RAG document input fields are incomplete or unknown'
    ERROR_INVALID_RAG_DOCUMENT_INPUT = 'Invalid RAG document input'

    ABSENT = None
    INVALID_BATCH = "documents must be a nonempty list"
    INVALID_SOURCE = "documents[{position}] has unsupported fields"
    DUPLICATE_ID = "Duplicate document identifier: {identifier}"
    class Field:
        IDENTIFIER = "identifier"
        TITLE = RAGDocument.Field.TITLE
        CONTENT = SplitDocument.Field.CONTENT
        METADATA = RAGDocument.Field.METADATA
        DESCRIPTION = RAGDocument.Field.DESCRIPTION


    REQUIRED = frozenset((Field.IDENTIFIER, RAGDocument.Field.TITLE, SplitDocument.Field.CONTENT, RAGDocument.Field.METADATA))

    @classmethod
    def create(cls, title, content, metadata, identifier):
        return {cls.Field.TITLE: title, cls.Field.CONTENT: content,
                cls.Field.METADATA: metadata, cls.Field.IDENTIFIER: identifier}

    @classmethod
    def prepare_batch(cls, documents):
        """Validate the entire explicit create batch before model calls or queue admission."""
        from .component import RAGComponent
        from llm.components.base import validate_name
        if not isinstance(documents, list) or not documents:
            raise ValueError(cls.INVALID_BATCH)
        result, identifiers = [], set()
        for position, source in enumerate(documents):
            if not isinstance(source, dict):
                raise ValueError(cls.INVALID_SOURCE.format(position=position))
            item = deepcopy(source)
            identifier = uuid4().hex if item.get(cls.Field.IDENTIFIER) is cls.ABSENT else validate_name(item[cls.Field.IDENTIFIER])
            if identifier in identifiers:
                raise ValueError(cls.DUPLICATE_ID.format(identifier=identifier))
            identifiers.add(identifier)
            item.update({cls.Field.IDENTIFIER: identifier, cls.Field.METADATA: {} if item.get(cls.Field.METADATA) is cls.ABSENT else item[cls.Field.METADATA]})
            RAGComponent.validate_input(item)
            result.append(item)
        return result

    @classmethod
    def validate(cls, source):
        """Require a complete original document input without creation defaults."""
        if (not isinstance(source, dict) or not cls.REQUIRED <= source.keys()
                or source.keys() - cls.REQUIRED - {RAGDocument.Field.DESCRIPTION}):
            raise ValueError(RAGDocumentInput.ERROR_RAG_DOCUMENT_INPUT_FIELDS_ARE_INCOMPLETE_OR_UNKNOWN)
        JsonObject.validate(source)
        validate_name(source[RAGDocumentInput.Field.IDENTIFIER])
        if (any(not isinstance(source[key], str) or not source[key].strip() for key in (RAGDocument.Field.TITLE, SplitDocument.Field.CONTENT))
                or not isinstance(source[RAGDocument.Field.METADATA], dict)
                or (source.get(RAGDocument.Field.DESCRIPTION) is not RAGDocumentInput.ABSENT and not isinstance(source[RAGDocument.Field.DESCRIPTION], str))):
            raise ValueError(RAGDocumentInput.ERROR_INVALID_RAG_DOCUMENT_INPUT)
        return source
