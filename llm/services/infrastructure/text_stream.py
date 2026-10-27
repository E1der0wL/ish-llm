"""Bounded UTF-8 decoding. Buffer sizes affect I/O only, never retained content."""

import codecs


BUFFER_BYTES = 64 * 1024


def text_blocks(stream, *, errors="strict", final=True):
    decoder = codecs.getincrementaldecoder("utf-8")(errors=errors)
    while block := stream.read(BUFFER_BYTES):
        value = decoder.decode(block)
        if value:
            yield value
    tail = decoder.decode(b"", final=final)
    if tail:
        yield tail


def read_text_range(stream, *, offset, limit, errors="strict", final=True):
    if type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("Text range requires nonnegative character offset and limit")
    if limit == 0:
        return ""
    parts, skipped, count = [], 0, 0
    for block in text_blocks(stream, errors=errors, final=final):
        if skipped + len(block) <= offset:
            skipped += len(block)
            continue
        value = block[max(0, offset - skipped):]
        skipped += len(block)
        if limit is not None:
            value = value[:limit - count]
        parts.append(value)
        count += len(value)
        if limit is not None and count == limit:
            break
    return "".join(parts)
