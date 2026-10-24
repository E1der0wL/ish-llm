"""Markdown 제목 계층과 원문을 보존하는 유한 크기 문단 분할."""

import re
from typing import NamedTuple

from .chunk_records import RAGSection, RAGChunk, SplitDocument


class MarkdownHeading(NamedTuple):
    start: int
    level: int
    title: str


class HeadingAncestor(NamedTuple):
    level: int
    title: str


class MarkdownSplitter:
    FENCE = r"^ {0,3}(`{3,}|~{3,})"
    HEADING = r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$"
    PARAGRAPH = r"\n\s*\n"
    INVALID_CONTENT = "Document content must be nonempty text"
    INVALID_SIZE = "chunk_size must be a positive integer"
    MINIMUM_SIZE = 1
    FIRST = 0
    LAST = -1
    NEXT = 1
    ROOT_LEVEL = 0
    ROOT_TITLE = ""
    NO_FENCE = None
    KEEP_ENDS = True
    FENCE_TOKEN = 1
    HEADING_MARKERS = 1
    HEADING_TITLE = 2

    @classmethod
    def split(cls, content: str, document_id: str, *, chunk_size: int) -> dict:
        """ATX 제목/문단을 분할한다. 코드 펜스 내부의 #는 제목으로 취급하지 않는다."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError(cls.INVALID_CONTENT)
        if type(chunk_size) is not int or chunk_size < cls.MINIMUM_SIZE:
            raise ValueError(cls.INVALID_SIZE)
        sections, chunks, stack, headings = {}, [], [], []
        offset, fence = cls.FIRST, cls.NO_FENCE
        for line in content.splitlines(keepends=cls.KEEP_ENDS):
            marker = re.match(cls.FENCE, line)
            if marker:
                token = marker.group(cls.FENCE_TOKEN)
                if fence is cls.NO_FENCE:
                    fence = token
                elif token[cls.FIRST] == fence[cls.FIRST] and len(token) >= len(fence):
                    fence = cls.NO_FENCE
            elif fence is cls.NO_FENCE:
                heading = re.match(cls.HEADING, line.rstrip())
                if heading:
                    headings.append(MarkdownHeading(offset, len(heading.group(cls.HEADING_MARKERS)), heading.group(cls.HEADING_TITLE)))
            offset += len(line)
        boundaries = [MarkdownHeading(cls.FIRST, cls.ROOT_LEVEL, cls.ROOT_TITLE)] if not headings or headings[cls.FIRST].start else []
        boundaries += headings
        for index, (start, level, title) in enumerate(boundaries):
            end = boundaries[index + cls.NEXT].start if index + cls.NEXT < len(boundaries) else len(content)
            while stack and stack[cls.LAST].level >= level:
                stack.pop()
            if title:
                stack.append(HeadingAncestor(level, title))
            section_id = RAGSection.identifier_for(document_id, index + RAGSection.FIRST_ORDINAL)
            # 소속 제목의 하위 절도 함께 반환할 수 있도록 전체 범위를 보존한다.
            parent_end = next((pos for pos, depth, _ in boundaries[index + cls.NEXT:]
                               if depth <= level), len(content))
            sections[section_id] = RAGSection.create(section_id, title,
                [ancestor.title for ancestor in stack], content[start:parent_end])
            for paragraph in re.split(cls.PARAGRAPH, content[start:end]):
                for part in range(cls.FIRST, len(paragraph), chunk_size):
                    text = paragraph[part:part + chunk_size].strip()
                    if text:
                        chunks.append(RAGChunk.create(RAGChunk.identifier_for(document_id, len(chunks) + RAGChunk.FIRST_ORDINAL),
                            document_id, section_id, title, text))
        return SplitDocument.create(content, sections, chunks)


def split_markdown(content: str, document_id: str, *, chunk_size: int) -> dict:
    """ATX 제목/문단을 분할한다. 코드 펜스 내부의 #는 제목으로 취급하지 않는다."""
    return MarkdownSplitter.split(content, document_id, chunk_size=chunk_size)
