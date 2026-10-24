"""SDK exception observation fields; readers do not classify or authorize retries."""

from collections.abc import Mapping


class SDKError:
    RESPONSE = "response"
    HEADERS = "headers"
    STATUS_CODE = "status_code"
    REQUEST_ID = "request_id"
    BODY = "body"
    ERROR = "error"
    MESSAGE = "message"
    TYPE = "type"
    CODE = "code"
    PARAM = "param"
    DETAIL_FIELDS = (MESSAGE, TYPE, CODE, PARAM)
    HEADER_ITEMS = Mapping.items.__name__
    UNSPECIFIED = None

    def __init__(self, value):
        self._value = value

    @property
    def headers(self):
        response = getattr(self._value, self.RESPONSE, self.UNSPECIFIED)
        return getattr(response, self.HEADERS, self.UNSPECIFIED) or getattr(
            self._value, self.HEADERS, self.UNSPECIFIED
        )

    @property
    def status(self):
        return getattr(self._value, self.STATUS_CODE, self.UNSPECIFIED)

    @property
    def request_id(self):
        return getattr(self._value, self.REQUEST_ID, self.UNSPECIFIED)

    @property
    def body(self):
        body = getattr(self._value, self.BODY, self.UNSPECIFIED)
        if isinstance(body, Mapping) and isinstance(body.get(self.ERROR), Mapping):
            body = body[self.ERROR]
        return body

    @classmethod
    def body_message(cls, body):
        return body.get(cls.MESSAGE) if isinstance(body, Mapping) else cls.UNSPECIFIED
