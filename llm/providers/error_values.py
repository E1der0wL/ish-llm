"""SDK exception observation fields; readers do not classify or authorize retries."""


class SDKError:
    RESPONSE = "response"
    HEADERS = "headers"
    STATUS_CODE = "status_code"
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
