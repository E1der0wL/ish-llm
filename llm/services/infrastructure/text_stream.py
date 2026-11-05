"""Bounded UTF-8 decoding. Buffer sizes affect I/O only, never retained content."""

import codecs


BUFFER_BYTES = 64 * 1024
ENCODING = "utf-8"
STRICT_ERRORS = "strict"
COMPLETE = True
INCOMPLETE = False
EMPTY_BYTES = b""
MINIMUM_OFFSET = 0
MINIMUM_LIMIT = 0


INVALID_RANGE = 'Text range requires nonnegative character offset and limit'

def text_blocks(stream, *, errors=STRICT_ERRORS, final=COMPLETE):
    decoder = codecs.getincrementaldecoder(ENCODING)(errors=errors)
    while block := stream.read(BUFFER_BYTES):
        value = decoder.decode(block)
        if value:
            yield value
    tail = decoder.decode(EMPTY_BYTES, final=final)
    if tail:
        yield tail


def read_text_range(stream, *, offset, limit, errors=STRICT_ERRORS, final=COMPLETE):
    if type(offset) is not int or offset < MINIMUM_OFFSET or limit is not None and (type(limit) is not int or limit < MINIMUM_LIMIT):
        raise ValueError(INVALID_RANGE)
    if limit == MINIMUM_LIMIT:
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
