"""OCR result, Tesseract protocol and image-model response value contracts."""

import base64
import csv
import math
from dataclasses import dataclass
from io import StringIO
from llm.errors import CodedError
from llm.core.json_data import JsonObject
from llm.core.model_protocol import ContentKind, ContentPart
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from .image_contracts import ImageGeometry


class VisionErrorCode:
    INVALID_RESPONSE = "vision_invalid_response"
    BACKEND_UNAVAILABLE = "vision_backend_unavailable"
    OCR_FAILED = "vision_ocr_failed"


class VisionError(CodedError, ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OCRBlock:
    TEXT = "text"
    BOX = "box"
    CONFIDENCE = "confidence"
    ABSENT = None
    INVALID_BOX = "OCR block requires an in-image pixel box"
    INVALID_CONFIDENCE = "Invalid OCR confidence"

    @classmethod
    def validate(cls, block, width, height):
        box = block.get(cls.BOX) if isinstance(block, dict) else cls.ABSENT
        if (
            not isinstance(block, dict)
            or not isinstance(block.get(cls.TEXT), str)
            or not isinstance(box, list)
            or len(box) != ImageGeometry.BOX_LENGTH
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)
            or not (
                ImageGeometry.ORIGIN
                <= box[ImageGeometry.LEFT_INDEX]
                < box[ImageGeometry.RIGHT_INDEX]
                <= width
                and ImageGeometry.ORIGIN
                <= box[ImageGeometry.TOP_INDEX]
                < box[ImageGeometry.BOTTOM_INDEX]
                <= height
            )
        ):
            raise VisionError(VisionErrorCode.INVALID_RESPONSE, cls.INVALID_BOX)
        score = block.get(cls.CONFIDENCE)
        if score is not cls.ABSENT and (type(score) not in (int, float) or not math.isfinite(score)):
            raise VisionError(VisionErrorCode.INVALID_RESPONSE, cls.INVALID_CONFIDENCE)

    @classmethod
    def word(cls, text, left, top, width, height, confidence):
        return {cls.TEXT: text, cls.BOX: [left, top, left + width, top + height], cls.CONFIDENCE: confidence}


class OCRResult:
    TEXT = OCRBlock.TEXT
    BLOCKS = "blocks"
    INVALID = "OCR requires text and blocks"

    @classmethod
    def validate(cls, result: dict, width: int, height: int) -> dict:
        JsonObject.validate(result)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get(cls.TEXT), str)
            or not isinstance(result.get(cls.BLOCKS), list)
        ):
            raise VisionError(VisionErrorCode.INVALID_RESPONSE, cls.INVALID)
        for block in result[cls.BLOCKS]:
            OCRBlock.validate(block, width, height)
        return result


class TesseractOptions:
    LANGUAGE = "language"
    PAGE_SEGMENTATION = "page_segmentation"
    VARIABLES = "variables"
    MINIMUM_LANGUAGE = 1
    MINIMUM_SEGMENTATION = 0
    MAXIMUM_SEGMENTATION = 13

    def __init__(self, value):
        # The selected backend's describe_config validator runs before this projection.
        self._value = value

    @property
    def has_language(self):
        return self.LANGUAGE in self._value

    @property
    def language(self):
        return self._value[self.LANGUAGE]

    @property
    def has_page_segmentation(self):
        return self.PAGE_SEGMENTATION in self._value

    @property
    def page_segmentation(self):
        return self._value[self.PAGE_SEGMENTATION]

    @property
    def variables(self):
        return self._value.get(self.VARIABLES, {})

    @classmethod
    def schema(cls):
        return {
            SchemaKeyword.TYPE: SchemaType.OBJECT,
            SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
            SchemaKeyword.PROPERTIES: {
                cls.LANGUAGE: {
                    SchemaKeyword.TYPE: SchemaType.STRING,
                    SchemaKeyword.MIN_LENGTH: cls.MINIMUM_LANGUAGE,
                },
                cls.PAGE_SEGMENTATION: {
                    SchemaKeyword.TYPE: SchemaType.INTEGER,
                    SchemaKeyword.MINIMUM: cls.MINIMUM_SEGMENTATION,
                    SchemaKeyword.MAXIMUM: cls.MAXIMUM_SEGMENTATION,
                },
                cls.VARIABLES: {
                    SchemaKeyword.TYPE: SchemaType.OBJECT,
                    SchemaKeyword.ADDITIONAL_PROPERTIES: {SchemaKeyword.TYPE: SchemaType.STRING},
                },
            },
        }


class TesseractWire:
    STANDARD_INPUT = "stdin"
    STANDARD_OUTPUT = "stdout"
    LANGUAGE_FLAG = "-l"
    SEGMENTATION_FLAG = "--psm"
    VARIABLE_FLAG = "-c"
    ASSIGNMENT = "="
    TSV = "tsv"
    TEXT_ENCODING = "utf-8"
    DELIMITER = "\t"
    WORD_LEVEL = 5
    WORD_SEPARATOR = " "
    LINE_SEPARATOR = "\n"
    CONFIDENCE_FIELD = "confidence_kind"
    CONFIDENCE_KIND = "tesseract_word_confidence_0_100"
    INVALID_VARIABLE = "Invalid Tesseract variable name"
    MISSING_HEADER = "TSV header is missing"
    INVALID_TSV = "Invalid Tesseract TSV response"

    class Field:
        LEVEL = "level"
        PAGE = "page_num"
        BLOCK = "block_num"
        PARAGRAPH = "par_num"
        LINE = "line_num"
        LEFT = "left"
        TOP = "top"
        WIDTH = "width"
        HEIGHT = "height"
        CONFIDENCE = "conf"
        TEXT = OCRBlock.TEXT

    REQUIRED = {
        Field.LEVEL,
        Field.PAGE,
        Field.BLOCK,
        Field.PARAGRAPH,
        Field.LINE,
        Field.LEFT,
        Field.TOP,
        Field.WIDTH,
        Field.HEIGHT,
        Field.CONFIDENCE,
        Field.TEXT,
    }
    BOX_FIELDS = (Field.LEFT, Field.TOP, Field.WIDTH, Field.HEIGHT)
    LINE_FIELDS = (Field.PAGE, Field.BLOCK, Field.PARAGRAPH, Field.LINE)

    @classmethod
    def arguments(cls, executable, options):
        value = TesseractOptions(options)
        argv = [executable, cls.STANDARD_INPUT, cls.STANDARD_OUTPUT]
        if value.has_language:
            argv += [cls.LANGUAGE_FLAG, value.language]
        if value.has_page_segmentation:
            argv += [cls.SEGMENTATION_FLAG, str(value.page_segmentation)]
        for key, item in value.variables.items():
            if not key or cls.ASSIGNMENT in key or any(c.isspace() for c in key):
                raise ValueError(cls.INVALID_VARIABLE)
            argv += [cls.VARIABLE_FLAG, key + cls.ASSIGNMENT + item]
        argv.append(cls.TSV)
        return argv

    @classmethod
    def read(cls, stdout):
        try:
            rows = csv.DictReader(
                StringIO(stdout.decode(cls.TEXT_ENCODING)), delimiter=cls.DELIMITER, quoting=csv.QUOTE_NONE
            )
            if not cls.REQUIRED.issubset(rows.fieldnames or ()):
                raise ValueError(cls.MISSING_HEADER)
            blocks, lines = [], {}
            for row in rows:
                if int(row[cls.Field.LEVEL]) != cls.WORD_LEVEL or not row[cls.Field.TEXT].strip():
                    continue
                left, top, width, height = (int(row[key]) for key in cls.BOX_FIELDS)
                blocks.append(
                    OCRBlock.word(
                        row[cls.Field.TEXT], left, top, width, height, float(row[cls.Field.CONFIDENCE])
                    )
                )
                key = tuple(row[k] for k in cls.LINE_FIELDS)
                lines.setdefault(key, []).append(row[cls.Field.TEXT])
            return {
                OCRResult.TEXT: cls.LINE_SEPARATOR.join(
                    cls.WORD_SEPARATOR.join(words) for words in lines.values()
                ),
                OCRResult.BLOCKS: blocks,
                cls.CONFIDENCE_FIELD: cls.CONFIDENCE_KIND,
            }
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise VisionError(VisionErrorCode.INVALID_RESPONSE, cls.INVALID_TSV) from error


class InlineImage:
    PREFIX = "data:"
    SEPARATOR = ";base64,"
    ENCODING = "ascii"

    @classmethod
    def part(cls, raw: bytes, mime_type: str) -> ContentPart:
        return ContentPart(
            ContentKind.IMAGE,
            cls.PREFIX + mime_type + cls.SEPARATOR + base64.b64encode(raw).decode(cls.ENCODING),
            mime_type,
        )


@dataclass(frozen=True, slots=True)
class VisionAnalysis:
    text: str
    model: str | None
    finish_reason: str | None

    @classmethod
    def from_response(cls, response):
        return cls(response.message.text, response.model, response.raw_finish_reason)

    def to_dict(self):
        owner = type(self)
        return {
            owner.text.__name__: self.text,
            owner.model.__name__: self.model,
            owner.finish_reason.__name__: self.finish_reason,
        }
