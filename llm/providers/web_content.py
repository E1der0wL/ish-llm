"""Web observations and incremental HTML extraction; source text is not authority."""

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from llm.core.contracts import JsonValue
from llm.core.schema import object_schema


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError('Web URL must be text')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Web URL requires HTTP or HTTPS and a host')
    return value


@dataclass(frozen=True, slots=True)
class WebSource(JsonValue):
    url: str
    title: str
    snippet: str

    def __post_init__(self):
        super(WebSource, self).__post_init__()
        validate_url(self.url)


@dataclass(frozen=True, slots=True)
class SearchPage(JsonValue):
    results: list[WebSource]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class WebPage(JsonValue):
    requested_url: str
    url: str
    title: str
    text: str
    links: list[WebSource]
    content_type: str
    sha256: str
    truncated: bool


class WebArguments:
    SEARCH = object_schema({'query': {'type': 'string', 'minLength': 1},
                            'limit': {'type': 'integer', 'minimum': 1},
                            'cursor': {'type': 'string'}}, required=['query'])
    FETCH = object_schema({'url': {'type': 'string', 'minLength': 1},
                           'max_bytes': {'type': 'integer', 'minimum': 1},
                           'max_chars': {'type': 'integer', 'minimum': 0}}, required=['url'])


class PageText(HTMLParser):
    """Extract text/title/links without executing scripts or fetching subresources."""

    HIDDEN = frozenset(('script', 'style', 'template'))
    BREAKS = frozenset(('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article'))
    VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'))

    def __init__(self, url, max_chars):
        super().__init__(convert_charrefs=True)
        self.url, self.max_chars = url, max_chars
        self.parts, self.title, self.links, self.hidden = [], [], [], []
        self.length, self.truncated, self.in_title = 0, False, False
        self.anchor = None

    def text(self, value):
        room = len(value) if self.max_chars is None else max(0, self.max_chars - self.length)
        selected = value[:room]
        if selected:
            self.parts.append(selected)
        self.length += len(selected)
        self.truncated |= len(selected) != len(value)

    def handle_starttag(self, tag, attrs):
        if self.hidden or tag in self.HIDDEN:
            if tag not in self.VOID:
                self.hidden.append(tag)
            return
        if tag == 'title':
            self.in_title = True
        elif tag in self.BREAKS:
            self.text('\n')
        if tag == 'a':
            href = dict(attrs).get('href')
            if href is not None:
                try:
                    target = validate_url(urljoin(self.url, href))
                except ValueError:
                    return
                self.anchor = target, []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in self.hidden:
                while self.hidden.pop() != tag:
                    pass
            return
        if tag == 'title':
            self.in_title = False
        if tag == 'a' and self.anchor is not None:
            target, parts = self.anchor
            self.links.append(WebSource(target, ''.join(parts).strip(), ''))
            self.anchor = None
        if tag in self.BREAKS:
            self.text('\n')

    def handle_data(self, value):
        if self.hidden:
            return
        if self.in_title:
            self.title.append(value)
        else:
            self.text(value)
        if self.anchor is not None:
            self.anchor[1].append(value)
