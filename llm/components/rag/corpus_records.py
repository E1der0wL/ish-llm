"""Current RAG corpus identity and required document container."""
class RAGCorpus:
    IDENTITY_WIDTH = 2

    class Field:
        IDENTITY = "identity"
        GENERATION = "generation"
        DOCUMENTS = "documents"

    def __init__(self, value):
        self._value = value

    @property
    def documents(self):
        return self._value[self.Field.DOCUMENTS]

    @property
    def binding(self):
        return self._value[self.Field.IDENTITY], self._value[self.Field.GENERATION]

