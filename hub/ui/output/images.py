"""Bounded background ASCII conversion; never print or fetch remote images."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .registry import literal_lines


class ImageRenderer:
    def __init__(self):
        self._executor = None
        self._cache = OrderedDict()
        self._pending = set()
        self._artifacts = {}
        self._lock = Lock()
        self._closed = False
        self._scope = None
        self._generation = 0
        self.revision = 0

    def register_artifact(self, identifier: str, path: str | Path):
        with self._lock:
            self._artifacts[identifier] = Path(path).expanduser().absolute()
            self._cache.clear()
            self._generation += 1
            self.revision += 1

    def reset(self):
        with self._lock:
            self._cache.clear()
            self._generation += 1
            self.revision += 1

    @staticmethod
    def _convert(root, source, artifact, width):
        from ascii_magic import AsciiArt
        from PIL import Image
        if artifact is not None:
            path = artifact.resolve()
        else:
            if ":" in source:
                raise ValueError("Use a project-relative image path or registered artifact ID")
            base = root.expanduser().resolve()
            path = (base / source).resolve()
            if not path.is_relative_to(base):
                raise ValueError("Image must be inside the project file root")
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Image is missing or exceeds 16 MiB")
        with Image.open(path) as image:
            if image.width * image.height > 8_000_000:
                raise ValueError("Image exceeds 8 million pixels")
            columns = max(1, min(width, 100, int(40 * image.width * 2.2 / max(1, image.height))))
            rows = max(1, min(40, int(image.height * columns / image.width / 2.2)))
            image.load()
            thumbnail = image.convert("RGB").resize((columns, rows))
            cells = AsciiArt.from_pillow_image(thumbnail).to_character_list(columns=columns, width_ratio=1, full_color=True)
        return [[("fg:" + cell["full-hex-color"], cell["character"]) for cell in row] for row in cells]

    def render(self, block, context):
        future = None
        attrs = dict(block.attributes)
        source = attrs.get("src", "")
        if not source or set(attrs) - {"src", "alt"} or block.text.strip():
            raise ValueError("hub-image requires src and accepts optional alt")
        with self._lock:
            # Transcript and enlarged object popup render concurrently at
            # different widths. A resize must not invalidate each other's work.
            scope = str(context.root)
            if scope != self._scope:
                self._scope = scope
                self._cache.clear()
                self._generation += 1
            artifact = self._artifacts.get(source[9:]) if source.startswith("artifact:") else None
            if source.startswith("artifact:") and artifact is None:
                raise ValueError("Unknown image artifact")
            key = (self._generation, scope, context.width, source, str(artifact))
            result = self._cache.get(key)
            limited = result is None and key not in self._pending and len(self._cache) + len(self._pending) >= 32
            if result is not None:
                self._cache.move_to_end(key)
            elif key not in self._pending and len(self._pending) < 4 and not limited and not self._closed:
                self._pending.add(key)
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hub-image")
                future = self._executor.submit(self._convert, context.root, source, artifact, context.width)
                # Attach outside the lock: a completed future invokes callbacks immediately.
            else:
                future = None
        if result is None and future is not None:
            def completed(done):
                try:
                    value = (done.result(), None)
                except Exception as error:
                    value = (None, str(error))
                with self._lock:
                    self._pending.discard(key)
                    if self._closed:
                        return
                    if key[0] == self._generation:
                        self._cache[key] = value
                    self.revision += 1
                context.invalidate()
            future.add_done_callback(completed)
        caption = attrs.get("alt") or source
        header = literal_lines(context.language("output_image", caption=caption), context.width, "class:hub.muted")
        if limited:
            return header + literal_lines(context.language("output_image_limit"), context.width, "class:hub.muted")
        if result is None:
            return header + literal_lines(context.language("output_image_loading"), context.width, "class:hub.muted")
        lines, error = result
        if error:
            return header + literal_lines(context.language("output_error", error=error), context.width, "class:hub.notice")
        return header + lines

    def close(self):
        with self._lock:
            self._closed = True
            self._cache.clear()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
