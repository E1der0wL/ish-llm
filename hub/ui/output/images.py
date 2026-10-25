"""Bounded background ASCII conversion; never print or fetch remote images."""

from hub.locales.keys import MessageKey

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .registry import literal_lines, RendererCapabilities
from .model import DisplayAttributes
from dataclasses import dataclass, fields
from typing import NamedTuple
from ...config.theme_style import ThemeSelector, ThemeStyle


@dataclass(frozen=True, slots=True)
class ImageAttributes(DisplayAttributes):
    INVALID_BLOCK = "hub-image requires src and accepts optional alt"

    src: str = ""
    alt: str | None = None

    @classmethod
    def from_block(cls, block):
        values = dict(block.attributes)
        result = cls.from_record(values)
        if (
            not result.src
            or set(values) - {field.name for field in fields(cls)}
            or block.text.strip()
        ):
            raise ValueError(cls.INVALID_BLOCK)
        return result


class ImageSource:
    ARTIFACT_PREFIX = "artifact:"
    ABSENT = None

    @classmethod
    def is_artifact(cls, source):
        return source.startswith(cls.ARTIFACT_PREFIX)

    @classmethod
    def artifact(cls, source, artifacts):
        return (
            artifacts.get(source[len(cls.ARTIFACT_PREFIX) :])
            if cls.is_artifact(source)
            else cls.ABSENT
        )


class AsciiMagicCell:
    COLOR = "full-hex-color"
    CHARACTER = "character"

    @classmethod
    def fragment(cls, value):
        return ThemeStyle.FOREGROUND_PREFIX + value[cls.COLOR], value[cls.CHARACTER]


class ImageCacheKey(NamedTuple):
    generation: int
    root: str
    width: int
    source: str
    artifact: str


class ImageResult(NamedTuple):
    lines: object
    error: object


class ImageRenderer:
    ABSENT = None
    OPEN = False
    CLOSED = True
    INITIAL_GENERATION = 0
    REVISION_STEP = 1
    PENDING_REVISION = 0
    SCHEME_SEPARATOR = ":"
    INVALID_SOURCE = "Use a project-relative image path or registered artifact ID"
    OUTSIDE_ROOT = "Image must be inside the project file root"
    MAX_FILE_BYTES = 16 * 1024 * 1024
    INVALID_FILE = "Image is missing or exceeds 16 MiB"
    MAX_PIXELS = 8_000_000
    INVALID_PIXELS = "Image exceeds 8 million pixels"
    MIN_DIMENSION = 1
    MAX_COLUMNS = 100
    MAX_ROWS = 40
    CELL_ASPECT = 2.2
    IMAGE_MODE = "RGB"
    WIDTH_RATIO = 1
    FULL_COLOR = True
    MAX_CACHED_AND_PENDING = 32
    MAX_PENDING = 4
    WORKERS = 2
    THREAD_NAME = "hub-image"
    UNKNOWN_ARTIFACT = "Unknown image artifact"
    WAIT_FOR_SHUTDOWN = False
    CANCEL_FUTURES = True

    def __init__(self):
        self._executor = self.ABSENT
        self._cache = OrderedDict()
        self._pending = set()
        self._artifacts = {}
        self._lock = Lock()
        self._closed = self.OPEN
        self._scope = self.ABSENT
        self._generation = self.INITIAL_GENERATION
        self.revision = RendererCapabilities.INITIAL_REVISION

    def register_artifact(self, identifier: str, path: str | Path):
        with self._lock:
            self._artifacts[identifier] = Path(path).expanduser().absolute()
            self._cache.clear()
            self._generation += self.REVISION_STEP
            self.revision += self.REVISION_STEP

    def reset(self):
        with self._lock:
            self._cache.clear()
            self._generation += self.REVISION_STEP
            self.revision += self.REVISION_STEP

    @staticmethod
    def _convert(root, source, artifact, width):
        from ascii_magic import AsciiArt
        from PIL import Image

        if artifact is not ImageRenderer.ABSENT:
            path = artifact.resolve()
        else:
            if ImageRenderer.SCHEME_SEPARATOR in source:
                raise ValueError(ImageRenderer.INVALID_SOURCE)
            base = root.expanduser().resolve()
            path = (base / source).resolve()
            if not path.is_relative_to(base):
                raise ValueError(ImageRenderer.OUTSIDE_ROOT)
        if not path.is_file() or path.stat().st_size > ImageRenderer.MAX_FILE_BYTES:
            raise ValueError(ImageRenderer.INVALID_FILE)
        with Image.open(path) as image:
            if image.width * image.height > ImageRenderer.MAX_PIXELS:
                raise ValueError(ImageRenderer.INVALID_PIXELS)
            columns = max(
                ImageRenderer.MIN_DIMENSION,
                min(
                    width,
                    ImageRenderer.MAX_COLUMNS,
                    int(
                        ImageRenderer.MAX_ROWS
                        * image.width
                        * ImageRenderer.CELL_ASPECT
                        / max(ImageRenderer.MIN_DIMENSION, image.height)
                    ),
                ),
            )
            rows = max(
                ImageRenderer.MIN_DIMENSION,
                min(
                    ImageRenderer.MAX_ROWS,
                    int(
                        image.height * columns / image.width / ImageRenderer.CELL_ASPECT
                    ),
                ),
            )
            image.load()
            thumbnail = image.convert(ImageRenderer.IMAGE_MODE).resize((columns, rows))
            cells = AsciiArt.from_pillow_image(thumbnail).to_character_list(
                columns=columns,
                width_ratio=ImageRenderer.WIDTH_RATIO,
                full_color=ImageRenderer.FULL_COLOR,
            )
        return [[AsciiMagicCell.fragment(cell) for cell in row] for row in cells]

    def block_revision(self, block, context):
        source = ImageAttributes.from_pairs(block.attributes).src
        with self._lock:
            artifact = ImageSource.artifact(source, self._artifacts)
            key = ImageCacheKey(
                self._generation,
                str(context.root),
                context.width,
                source,
                str(artifact),
            )
            result = self._cache.get(key)
            # Unscheduled images may retry when a conversion releases a slot.
            revision = (
                id(result)
                if result is not self.ABSENT
                else (self.PENDING_REVISION if key in self._pending else self.revision)
            )
            return self._generation, revision

    def render(self, block, context):
        future = self.ABSENT
        attrs = ImageAttributes.from_block(block)
        source = attrs.src
        with self._lock:
            # Transcript and enlarged object popup render concurrently at
            # different widths. A resize must not invalidate each other's work.
            scope = str(context.root)
            if scope != self._scope:
                self._scope = scope
                self._cache.clear()
                self._generation += self.REVISION_STEP
            artifact = ImageSource.artifact(source, self._artifacts)
            if ImageSource.is_artifact(source) and artifact is self.ABSENT:
                raise ValueError(self.UNKNOWN_ARTIFACT)
            key = ImageCacheKey(
                self._generation, scope, context.width, source, str(artifact)
            )
            result = self._cache.get(key)
            limited = (
                result is self.ABSENT
                and key not in self._pending
                and len(self._cache) + len(self._pending) >= self.MAX_CACHED_AND_PENDING
            )
            if result is not self.ABSENT:
                self._cache.move_to_end(key)
            elif (
                key not in self._pending
                and len(self._pending) < self.MAX_PENDING
                and not limited
                and not self._closed
            ):
                self._pending.add(key)
                if self._executor is self.ABSENT:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self.WORKERS, thread_name_prefix=self.THREAD_NAME
                    )
                future = self._executor.submit(
                    self._convert, context.root, source, artifact, context.width
                )
                # Attach outside the lock: a completed future invokes callbacks immediately.
            else:
                future = self.ABSENT
        if result is self.ABSENT and future is not self.ABSENT:

            def completed(done):
                try:
                    value = ImageResult(done.result(), self.ABSENT)
                except Exception as error:
                    value = ImageResult(self.ABSENT, str(error))
                with self._lock:
                    self._pending.discard(key)
                    if self._closed:
                        return
                    if key.generation == self._generation:
                        self._cache[key] = value
                    self.revision += self.REVISION_STEP
                context.invalidate()

            future.add_done_callback(completed)
        caption = attrs.alt or source
        header = literal_lines(
            context.language(MessageKey.OUTPUT_IMAGE, caption=caption),
            context.width,
            ThemeSelector.MUTED.style,
        )
        if limited:
            return header + literal_lines(
                context.language(MessageKey.OUTPUT_IMAGE_LIMIT),
                context.width,
                ThemeSelector.MUTED.style,
            )
        if result is self.ABSENT:
            return header + literal_lines(
                context.language(MessageKey.OUTPUT_IMAGE_LOADING),
                context.width,
                ThemeSelector.MUTED.style,
            )
        lines, error = result
        if error:
            return header + literal_lines(
                context.language(MessageKey.OUTPUT_ERROR, error=error),
                context.width,
                ThemeSelector.NOTICE.style,
            )
        return header + lines

    def close(self):
        with self._lock:
            self._closed = self.CLOSED
            self._cache.clear()
        if self._executor:
            self._executor.shutdown(
                wait=self.WAIT_FOR_SHUTDOWN, cancel_futures=self.CANCEL_FUTURES
            )
