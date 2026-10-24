"""RAG split-document, section and chunk shapes and original-source bindings."""

import hashlib


class RAGContent:
    ENCODING = "utf-8"

    @classmethod
    def digest(cls, text):
        return hashlib.sha256(text.encode(cls.ENCODING)).hexdigest()


class RAGDocument:
    class Field:
        ID = "id"
        TITLE = "title"
        METADATA = "metadata"
        GENERATION = "generation"
        REVISION = "revision"
        DESCRIPTION = "description"

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


class RAGSection:
    FIRST_ORDINAL = 1
    ID_FORMAT = "{document_id}:s{ordinal}"
    INVALID = "Invalid prepared RAG section"

    class Field:
        ID = "id"
        HEADING = "heading"
        HEADING_PATH = "heading_path"
        TEXT = "text"

    FIELDS = frozenset((Field.ID, Field.HEADING, Field.HEADING_PATH, Field.TEXT))

    def __init__(self, value):
        self._value = value

    @classmethod
    def identifier_for(cls, document_id, ordinal):
        return cls.ID_FORMAT.format(document_id=document_id, ordinal=ordinal)

    @classmethod
    def create(cls, identifier, title, path, text):
        return {cls.Field.ID: identifier, cls.Field.HEADING: title,
                cls.Field.HEADING_PATH: path, cls.Field.TEXT: text}

    @classmethod
    def validate_source(cls, section, identifier, content):
        if (not isinstance(section, dict) or section.keys() != cls.FIELDS
                or section[cls.Field.ID] != identifier
                or not isinstance(section[cls.Field.HEADING], str) or not isinstance(section[cls.Field.HEADING_PATH], list)
                or any(not isinstance(item, str) for item in section[cls.Field.HEADING_PATH])
                or not isinstance(section[cls.Field.TEXT], str) or not section[cls.Field.TEXT]
                or section[cls.Field.TEXT] not in content):
            raise ValueError(cls.INVALID)

    @property
    def heading(self):
        return self._value[self.Field.HEADING]

    @property
    def text(self):
        return self._value[self.Field.TEXT]


class RAGChunk:
    FIRST_ORDINAL = 1
    ID_FORMAT = "{document_id}:c{ordinal}"
    INVALID = "Invalid stored RAG chunk"
    INVALID_SOURCE = "Invalid prepared RAG chunk source"

    class Field:
        ID = "id"
        DOCUMENT_ID = "document_id"
        SECTION_ID = "section_id"
        HEADING = "heading"
        TEXT = "text"

    FIELDS = frozenset((Field.ID, Field.DOCUMENT_ID, Field.SECTION_ID, Field.HEADING, Field.TEXT))
    NONEMPTY_FIELDS = FIELDS - {Field.HEADING}

    def __init__(self, value):
        self._value = value

    @classmethod
    def identifier_for(cls, document_id, ordinal):
        return cls.ID_FORMAT.format(document_id=document_id, ordinal=ordinal)

    @classmethod
    def create(cls, identifier, document_id, section_id, heading, text):
        return {cls.Field.ID: identifier, cls.Field.DOCUMENT_ID: document_id,
                cls.Field.SECTION_ID: section_id, cls.Field.HEADING: heading, cls.Field.TEXT: text}

    @classmethod
    def validate(cls, chunk):
        """Stored shape; validating its source remains an explicit read boundary."""
        if (not isinstance(chunk, dict) or chunk.keys() != cls.FIELDS
                or any(not isinstance(chunk[key], str) for key in cls.FIELDS)
                or any(not chunk[key].strip() for key in cls.NONEMPTY_FIELDS)):
            raise ValueError(cls.INVALID)
        return chunk

    @classmethod
    def validate_source(cls, chunk, *, document_id, ordinal, sections):
        if (not isinstance(chunk, dict) or chunk.keys() != cls.FIELDS
                or chunk[cls.Field.ID] != cls.identifier_for(document_id, ordinal)
                or chunk[cls.Field.DOCUMENT_ID] != document_id
                or not isinstance(chunk[cls.Field.SECTION_ID], str) or chunk[cls.Field.SECTION_ID] not in sections
                or chunk[cls.Field.HEADING] != RAGSection(sections[chunk[cls.Field.SECTION_ID]]).heading
                or not isinstance(chunk[cls.Field.TEXT], str) or not chunk[cls.Field.TEXT].strip()
                or chunk[cls.Field.TEXT] not in RAGSection(sections[chunk[cls.Field.SECTION_ID]]).text):
            raise ValueError(cls.INVALID_SOURCE)

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def document_id(self):
        return self._value[self.Field.DOCUMENT_ID]

    @property
    def section_id(self):
        return self._value[self.Field.SECTION_ID]

    @property
    def heading(self):
        return self._value[self.Field.HEADING]

    @property
    def text(self):
        return self._value[self.Field.TEXT]


class SplitDocument:
    class Field:
        CONTENT = "content"
        SECTIONS = "sections"
        CHUNKS = "chunks"
        SHA256 = "sha256"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, content, sections, chunks):
        return {cls.Field.CONTENT: content, cls.Field.SECTIONS: sections,
                cls.Field.CHUNKS: chunks, cls.Field.SHA256: RAGContent.digest(content)}

    @property
    def chunks(self):
        return self._value[self.Field.CHUNKS]

    @property
    def sections(self):
        return self._value[self.Field.SECTIONS]

    @property
    def content(self):
        return self._value[self.Field.CONTENT]
