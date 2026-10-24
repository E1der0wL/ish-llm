"""Image formats, geometry, explicit limits and ordered transform input contracts."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from jsonschema import Draft202012Validator
from llm.core.json_data import JsonObject
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean


class ImageEncoding:
    PNG = "PNG"
    JPEG = "JPEG"
    FORMATS = (PNG, JPEG)
    PNG_MIME = "image/png"
    JPEG_MIME = "image/jpeg"
    MIME_TYPES = (PNG_MIME, JPEG_MIME)
    RGB = "RGB"
    PNG_MODES = ("1", "L", "LA", "P", RGB, "RGBA", "I", "I;16")
    JPEG_MODES = ("L", RGB, "CMYK")
    OUTPUT_FORMAT = PNG
    FRAME_ATTRIBUTE = "n_frames"
    SINGLE_FRAME = 1
    INVALID_IMAGE = "Only single-frame PNG/JPEG images are supported"
    JPEG_COLOR_REQUIRED = (
        "JPEG requires an explicit compatible color conversion; transparency is not discarded"
    )


class ImageGeometry:
    ORIGIN = 0
    MINIMUM_DIMENSION = 1
    WIDTH_INDEX = 0
    HEIGHT_INDEX = 1
    BOX_LENGTH = 4
    QUARTER_TURN = 90
    HALF_TURN = 180
    THREE_QUARTER_TURN = 270
    ROTATIONS = (QUARTER_TURN, HALF_TURN, THREE_QUARTER_TURN)


class ImageLimits:
    MAX_BYTES = "max_bytes"
    MAX_PIXELS = "max_pixels"
    NO_LIMIT = None
    OVERFLOW_PROBE = 1
    REQUIRED_BYTES = "Image bytes are required"
    EXCEEDED_BYTES = "Image exceeds configured max_bytes"
    INVALID_DIMENSIONS = "Image dimensions must be positive"
    EXCEEDED_PIXELS = "Image exceeds configured max_pixels"

    def __init__(self, value):
        self._value = value

    @property
    def max_bytes(self):
        return self._value.get(self.MAX_BYTES)

    def validate_bytes(self, raw):
        if not isinstance(raw, bytes) or not raw:
            raise ValueError(self.REQUIRED_BYTES)
        if self._value.get(self.MAX_BYTES) is not self.NO_LIMIT and len(raw) > self._value[self.MAX_BYTES]:
            raise ValueError(self.EXCEEDED_BYTES)

    def check_size(self, size):
        if min(size) < ImageGeometry.MINIMUM_DIMENSION:
            raise ValueError(self.INVALID_DIMENSIONS)
        if self._value.get(self.MAX_PIXELS) is not self.NO_LIMIT and (
            size[ImageGeometry.WIDTH_INDEX] * size[ImageGeometry.HEIGHT_INDEX] > self._value[self.MAX_PIXELS]
        ):
            raise ValueError(self.EXCEEDED_PIXELS)


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Decoded image facts; storage adds identity/provenance without redefining these fields."""

    sha256: str
    mime_type: str
    width: int
    height: int
    bytes: int

    @classmethod
    def from_image(cls, raw, image, mime_types):
        return cls(
            hashlib.sha256(raw).hexdigest(), mime_types[image.format], image.width, image.height, len(raw)
        )

    def to_dict(self):
        owner = type(self)
        return {
            owner.sha256.__name__: self.sha256,
            owner.mime_type.__name__: self.mime_type,
            owner.width.__name__: self.width,
            owner.height.__name__: self.height,
            owner.bytes.__name__: self.bytes,
        }


class ImageOperationKind(StrEnum):
    CROP = "crop"
    RESIZE = "resize"
    ROTATE = "rotate"
    GRAYSCALE = "grayscale"
    EXIF_TRANSPOSE = "exif_transpose"
    CONTRAST = "contrast"
    SHARPEN = "sharpen"
    FORMAT = "format"


class ImageOperation:
    OPERATIONS = "operations"
    MINIMUM_OPERATIONS = 1
    POSITIVE_MINIMUM = 0
    LAST_INDEX_OFFSET = 1
    FORMAT_LAST = "Format encoding must be the last operation"
    INVALID_CROP = "Crop box must be inside the current image"

    class Field:
        OPERATION = "operation"
        BOX = "box"
        SCALE = "scale"
        DEGREES = "degrees"
        FACTOR = "factor"
        FORMAT = ImageOperationKind.FORMAT.value

    def __init__(self, value):
        # The complete ordered list is checked by validate before interpretation.
        self._value = value

    @property
    def name(self):
        return self._value[self.Field.OPERATION]

    @property
    def box(self):
        return self._value[self.Field.BOX]

    @property
    def scale(self):
        return self._value[self.Field.SCALE]

    @property
    def degrees(self):
        return self._value[self.Field.DEGREES]

    @property
    def factor(self):
        return self._value[self.Field.FACTOR]

    @property
    def format(self):
        return self._value[self.Field.FORMAT]

    def rotation(self, transpose):
        return {
            ImageGeometry.QUARTER_TURN: transpose.ROTATE_90,
            ImageGeometry.HALF_TURN: transpose.ROTATE_180,
            ImageGeometry.THREE_QUARTER_TURN: transpose.ROTATE_270,
        }[self.degrees]

    @classmethod
    def schema(cls) -> dict:
        def item(name, properties, required):
            return {
                SchemaKeyword.TYPE: SchemaType.OBJECT,
                SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
                SchemaKeyword.PROPERTIES: {cls.Field.OPERATION: {SchemaKeyword.CONST: name}, **properties},
                SchemaKeyword.REQUIRED: [cls.Field.OPERATION, *required],
            }

        positive = {
            SchemaKeyword.TYPE: SchemaType.NUMBER,
            SchemaKeyword.EXCLUSIVE_MINIMUM: cls.POSITIVE_MINIMUM,
        }
        return {
            SchemaKeyword.TYPE: SchemaType.ARRAY,
            SchemaKeyword.MIN_ITEMS: cls.MINIMUM_OPERATIONS,
            SchemaKeyword.ITEMS: {
                SchemaKeyword.ONE_OF: [
                    item(
                        ImageOperationKind.CROP.value,
                        {
                            cls.Field.BOX: {
                                SchemaKeyword.TYPE: SchemaType.ARRAY,
                                SchemaKeyword.ITEMS: {
                                    SchemaKeyword.TYPE: SchemaType.INTEGER,
                                    SchemaKeyword.MINIMUM: ImageGeometry.ORIGIN,
                                },
                                SchemaKeyword.MIN_ITEMS: ImageGeometry.BOX_LENGTH,
                                SchemaKeyword.MAX_ITEMS: ImageGeometry.BOX_LENGTH,
                            }
                        },
                        [cls.Field.BOX],
                    ),
                    item(ImageOperationKind.RESIZE.value, {cls.Field.SCALE: positive}, [cls.Field.SCALE]),
                    item(
                        ImageOperationKind.ROTATE.value,
                        {cls.Field.DEGREES: {SchemaKeyword.ENUM: list(ImageGeometry.ROTATIONS)}},
                        [cls.Field.DEGREES],
                    ),
                    item(ImageOperationKind.GRAYSCALE.value, {}, []),
                    item(ImageOperationKind.EXIF_TRANSPOSE.value, {}, []),
                    item(ImageOperationKind.CONTRAST.value, {cls.Field.FACTOR: positive}, [cls.Field.FACTOR]),
                    item(ImageOperationKind.SHARPEN.value, {cls.Field.FACTOR: positive}, [cls.Field.FACTOR]),
                    item(
                        ImageOperationKind.FORMAT.value,
                        {cls.Field.FORMAT: {SchemaKeyword.ENUM: list(ImageEncoding.FORMATS)}},
                        [cls.Field.FORMAT],
                    ),
                ]
            },
        }

    @classmethod
    def validate(cls, operations, schema_factory):
        JsonObject.validate({cls.OPERATIONS: operations})
        Draft202012Validator(schema_factory()).validate(operations)
