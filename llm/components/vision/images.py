"""PNG/JPEG 검증과 순서가 있는 전처리. 원본 바이트를 바꾸거나 파일을 저장하지 않는다."""

from io import BytesIO
import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator


def operation_schema() -> dict:
    def item(name, properties, required):
        return {"type": "object", "additionalProperties": False,
                "properties": {"operation": {"const": name}, **properties},
                "required": ["operation", *required]}
    positive = {"type": "number", "exclusiveMinimum": 0}
    return {"type": "array", "minItems": 1, "items": {"oneOf": [
        item("crop", {"box": {"type": "array", "items": {"type": "integer", "minimum": 0},
                              "minItems": 4, "maxItems": 4}}, ["box"]),
        item("resize", {"scale": positive}, ["scale"]),
        item("rotate", {"degrees": {"enum": [90, 180, 270]}}, ["degrees"]),
        item("grayscale", {}, []), item("exif_transpose", {}, []),
        item("contrast", {"factor": positive}, ["factor"]),
        item("sharpen", {"factor": positive}, ["factor"]),
        item("format", {"format": {"enum": ["PNG", "JPEG"]}}, ["format"]),
    ]}}


def read_source(source: str | Path | bytes, limits: dict) -> bytes:
    """호스트/UI가 지정한 파일 또는 bytes만 받는다. Tool은 등록된 이미지 ID만 사용한다."""
    if isinstance(source, bytes):
        raw = source
    else:
        path = Path(source).absolute()
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("Image source must be a regular file without links")
        maximum = limits.get("max_bytes")
        with path.open("rb") as stream:
            raw = stream.read() if maximum is None else stream.read(maximum + 1)
    validate_bytes(raw, limits)
    return raw


def validate_bytes(raw: bytes, limits: dict) -> None:
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("Image bytes are required")
    if limits.get("max_bytes") is not None and len(raw) > limits["max_bytes"]:
        raise ValueError("Image exceeds configured max_bytes")


def check_size(size: tuple[int, int], limits: dict) -> None:
    if min(size) < 1:
        raise ValueError("Image dimensions must be positive")
    if limits.get("max_pixels") is not None and size[0] * size[1] > limits["max_pixels"]:
        raise ValueError("Image exceeds configured max_pixels")


def inspect_image(raw: bytes, limits: dict) -> dict:
    from PIL import Image
    validate_bytes(raw, limits)
    with Image.open(BytesIO(raw)) as image:
        if image.format not in ("PNG", "JPEG") or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Only single-frame PNG/JPEG images are supported")
        check_size(image.size, limits)
        result = {"sha256": hashlib.sha256(raw).hexdigest(), "mime_type": Image.MIME[image.format],
                  "width": image.width, "height": image.height, "bytes": len(raw)}
        image.verify()
    # 헤더 검사만으로 손상된 픽셀 데이터를 등록하지 않는다.
    with Image.open(BytesIO(raw)) as image:
        image.load()
    return result


def preprocess(raw: bytes, operations: list[dict], limits: dict) -> tuple[bytes, dict]:
    """각 연산은 직전 결과 좌표를 사용한다. 가공본 기본 저장 표현은 손실 없는 PNG다."""
    from PIL import Image, ImageEnhance, ImageOps
    from llm.core.models import ProjectConfig
    ProjectConfig.validate_settings({"operations": operations})
    Draft202012Validator(operation_schema()).validate(operations)
    inspect_image(raw, limits)
    with Image.open(BytesIO(raw)) as original:
        image = original.copy()
    try:
        output_format = "PNG"
        for index, operation in enumerate(operations):
            name = operation["operation"]
            if name == "format":
                if index != len(operations) - 1:
                    raise ValueError("Format encoding must be the last operation")
                output_format = operation["format"]
                continue
            if name == "crop":
                left, top, right, bottom = operation["box"]
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise ValueError("Crop box must be inside the current image")
                changed = image.crop((left, top, right, bottom))
            elif name == "resize":
                size = tuple(round(value * operation["scale"]) for value in image.size)
                check_size(size, limits)
                changed = image.resize(size)
            elif name == "rotate":
                changed = image.transpose({90: Image.Transpose.ROTATE_90,
                    180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}[operation["degrees"]])
            elif name == "exif_transpose":
                changed = ImageOps.exif_transpose(image)
            elif name == "grayscale":
                changed = ImageOps.grayscale(image)
            else:
                enhancer = ImageEnhance.Contrast if name == "contrast" else ImageEnhance.Sharpness
                changed = enhancer(image).enhance(operation["factor"])
            image.close()
            image = changed
        # PNG가 지원하지 않는 JPEG CMYK 등은 전송 가능한 RGB로 변환한다.
        if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"):
            changed = image.convert("RGB")
            image.close()
            image = changed
        if output_format == "JPEG" and image.mode not in ("L", "RGB", "CMYK"):
            raise ValueError("JPEG requires an explicit compatible color conversion; transparency is not discarded")
        output = BytesIO()
        image.save(output, format=output_format)
        value = output.getvalue()
        return value, inspect_image(value, limits)
    finally:
        image.close()
