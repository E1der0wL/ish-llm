"""Vision result and extracted-document provenance Writers; no new execution records."""

from llm.components.rag.document_records import RAGDocumentInput
from llm.services.infrastructure.storage import revision_token
from .ocr_contracts import VisionAnalysis
from .configuration import OCRSettings
from .records import VisionImageIdentity


class VisionResult:
    TEXT = VisionAnalysis.text.__name__
    KIND = "kind"
    IMAGE = "image"
    BACKEND = OCRSettings.BACKEND
    BACKEND_REVISION = "backend_revision"
    OPTIONS = "options"
    COORDINATE_SPACE = "coordinate_space"
    PIXEL_COORDINATES = "image_pixels_top_left_ltrb"
    OCR = OCRSettings.NAME
    MODEL_ANALYSIS = "model_analysis"
    INVALID_PROMPT = "Image analysis prompt is required"

    def __init__(self, value):
        self._value = value

    @property
    def text(self):
        return self._value[self.TEXT]

    @classmethod
    def ocr(cls, result, reference, backend, revision, options):
        return {
            **result,
            cls.KIND: cls.OCR,
            cls.IMAGE: reference,
            cls.BACKEND: backend,
            cls.BACKEND_REVISION: revision,
            cls.OPTIONS: options,
            cls.COORDINATE_SPACE: cls.PIXEL_COORDINATES,
        }

    @classmethod
    def analysis(cls, result, reference):
        return {**result, cls.KIND: cls.MODEL_ANALYSIS, cls.IMAGE: reference}

    @classmethod
    def validate_prompt(cls, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(cls.INVALID_PROMPT)

    def provenance(self):
        return {k: v for k, v in self._value.items() if k != self.TEXT}

    def text_digest(self):
        return revision_token({self.TEXT: self.text})


class VisionExtraction:
    OCR = VisionResult.OCR
    ANALYSIS = "analysis"
    NAMESPACE = VisionImageIdentity.COMPONENT
    TEXT_DIGEST = "text_sha256"
    SOURCE_KIND = "source_kind"
    DERIVED_TEXT = "derived_text"
    PROJECT_ID = "project_id"
    INVALID_TITLE = "Extracted document title is required"
    OCR_PROMPT = "OCR does not accept a VLM prompt"
    ANALYSIS_OPTIONS = "Analysis requires prompt and does not select an OCR backend"
    INVALID_MODE = "Select document extraction mode: ocr or analysis"
    EMPTY_TEXT = "Image extraction produced no document text"

    @classmethod
    def validate_title(cls, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError(cls.INVALID_TITLE)

    @classmethod
    def document(cls, title, result, project_id):
        return RAGDocumentInput.content_source(
            title,
            result.text,
            {
                cls.NAMESPACE: {
                    **result.provenance(),
                    cls.TEXT_DIGEST: result.text_digest(),
                    cls.SOURCE_KIND: cls.DERIVED_TEXT,
                    cls.PROJECT_ID: project_id(),
                }
            },
        )
