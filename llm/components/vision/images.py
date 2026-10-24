"""PNG/JPEG 검증과 순서가 있는 전처리. 원본 바이트를 바꾸거나 파일을 저장하지 않는다."""

from io import BytesIO
from pathlib import Path

from .image_contracts import (
    ImageEncoding,
    ImageGeometry,
    ImageInfo,
    ImageLimits,
    ImageOperation,
    ImageOperationKind,
)


class ImageSource:
    READ_MODE = "rb"
    INVALID = "Image source must be a regular file without links"


# The public schema callable is the canonical operation declaration itself.
operation_schema = ImageOperation.schema


def read_source(source: str | Path | bytes, limits: dict) -> bytes:
    """호스트/UI가 지정한 파일 또는 bytes만 받는다. Tool은 등록된 이미지 ID만 사용한다."""
    if isinstance(source, bytes):
        raw = source
    else:
        path = Path(source).absolute()
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError(ImageSource.INVALID)
        maximum = ImageLimits(limits).max_bytes
        with path.open(ImageSource.READ_MODE) as stream:
            raw = (
                stream.read()
                if maximum is ImageLimits.NO_LIMIT
                else stream.read(maximum + ImageLimits.OVERFLOW_PROBE)
            )
    validate_bytes(raw, limits)
    return raw


def validate_bytes(raw: bytes, limits: dict) -> None:
    ImageLimits(limits).validate_bytes(raw)


def check_size(size: tuple[int, int], limits: dict) -> None:
    ImageLimits(limits).check_size(size)


def inspect_image(raw: bytes, limits: dict) -> dict:
    from PIL import Image

    validate_bytes(raw, limits)
    with Image.open(BytesIO(raw)) as image:
        if (
            image.format not in ImageEncoding.FORMATS
            or getattr(image, ImageEncoding.FRAME_ATTRIBUTE, ImageEncoding.SINGLE_FRAME)
            != ImageEncoding.SINGLE_FRAME
        ):
            raise ValueError(ImageEncoding.INVALID_IMAGE)
        check_size(image.size, limits)
        result = ImageInfo.from_image(raw, image, Image.MIME).to_dict()
        image.verify()
    # 헤더 검사만으로 손상된 픽셀 데이터를 등록하지 않는다.
    with Image.open(BytesIO(raw)) as image:
        image.load()
    return result


def preprocess(raw: bytes, operations: list[dict], limits: dict) -> tuple[bytes, dict]:
    """각 연산은 직전 결과 좌표를 사용한다. 가공본 기본 저장 표현은 손실 없는 PNG다."""
    from PIL import Image, ImageEnhance, ImageOps

    ImageOperation.validate(operations, operation_schema)
    inspect_image(raw, limits)
    with Image.open(BytesIO(raw)) as original:
        image = original.copy()
    try:
        output_format = ImageEncoding.OUTPUT_FORMAT
        for index, operation in enumerate(operations):
            operation = ImageOperation(operation)
            name = operation.name
            if name == ImageOperationKind.FORMAT.value:
                if index != len(operations) - ImageOperation.LAST_INDEX_OFFSET:
                    raise ValueError(ImageOperation.FORMAT_LAST)
                output_format = operation.format
                continue
            if name == ImageOperationKind.CROP.value:
                left, top, right, bottom = operation.box
                if not (
                    ImageGeometry.ORIGIN <= left < right <= image.width
                    and ImageGeometry.ORIGIN <= top < bottom <= image.height
                ):
                    raise ValueError(ImageOperation.INVALID_CROP)
                changed = image.crop((left, top, right, bottom))
            elif name == ImageOperationKind.RESIZE.value:
                size = tuple(round(value * operation.scale) for value in image.size)
                check_size(size, limits)
                changed = image.resize(size)
            elif name == ImageOperationKind.ROTATE.value:
                changed = image.transpose(operation.rotation(Image.Transpose))
            elif name == ImageOperationKind.EXIF_TRANSPOSE.value:
                changed = ImageOps.exif_transpose(image)
            elif name == ImageOperationKind.GRAYSCALE.value:
                changed = ImageOps.grayscale(image)
            else:
                enhancer = (
                    ImageEnhance.Contrast
                    if name == ImageOperationKind.CONTRAST.value
                    else ImageEnhance.Sharpness
                )
                changed = enhancer(image).enhance(operation.factor)
            image.close()
            image = changed
        # PNG가 지원하지 않는 JPEG CMYK 등은 전송 가능한 RGB로 변환한다.
        if image.mode not in ImageEncoding.PNG_MODES:
            changed = image.convert(ImageEncoding.RGB)
            image.close()
            image = changed
        if output_format == ImageEncoding.JPEG and image.mode not in ImageEncoding.JPEG_MODES:
            raise ValueError(ImageEncoding.JPEG_COLOR_REQUIRED)
        output = BytesIO()
        image.save(output, format=output_format)
        value = output.getvalue()
        return value, inspect_image(value, limits)
    finally:
        image.close()
