"""Markdown 제목 계층과 원문을 보존하는 유한 크기 문단 분할."""

import hashlib
import re


def split_markdown(content: str, document_id: str, *, chunk_size: int = 2000) -> dict:
    """ATX 제목/문단을 분할한다. 코드 펜스 내부의 #는 제목으로 취급하지 않는다."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Document content must be nonempty text")
    if type(chunk_size) is not int or chunk_size < 64:
        raise ValueError("chunk_size must be an integer >= 64")
    sections, chunks, stack, headings = {}, [], [], []
    offset, fence = 0, None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip())
            if heading:
                headings.append((offset, len(heading.group(1)), heading.group(2)))
        offset += len(line)
    boundaries = [(0, 0, "")] if not headings or headings[0][0] else []
    boundaries += headings
    for index, (start, level, title) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(content)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if title:
            stack.append((level, title))
        section_id = f"{document_id}:s{index + 1}"
        # 소속 제목의 하위 절도 함께 반환할 수 있도록 전체 범위를 보존한다.
        parent_end = next((pos for pos, depth, _ in boundaries[index + 1:]
                           if depth <= level), len(content))
        sections[section_id] = {"id": section_id, "heading": title,
            "heading_path": [name for _, name in stack], "text": content[start:parent_end]}
        for paragraph in re.split(r"\n\s*\n", content[start:end]):
            for part in range(0, len(paragraph), chunk_size):
                text = paragraph[part:part + chunk_size].strip()
                if text:
                    chunks.append({"id": f"{document_id}:c{len(chunks) + 1}",
                        "document_id": document_id, "section_id": section_id,
                        "heading": title, "text": text})
    return {"content": content, "sections": sections, "chunks": chunks,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
