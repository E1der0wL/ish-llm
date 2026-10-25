"""Host-owned binding and cache projections; existing revision payloads stay unchanged."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HubEnvironmentBinding:
    contract: str
    shell: str

    def to_dict(self):
        cls = type(self)
        return {cls.contract.__name__: self.contract, cls.shell.__name__: self.shell}


@dataclass(frozen=True, slots=True)
class HubRegistryBinding:
    options: dict
    definitions: list
    contracts: dict

    def to_dict(self):
        cls = type(self)
        return {
            cls.options.__name__: self.options,
            cls.definitions.__name__: self.definitions,
            cls.contracts.__name__: self.contracts,
        }


class ResolvedToolkit:
    """Borrow the existing (revision, registry) cache entry without changing its shape."""

    REVISION_POSITION = 0
    REGISTRY_POSITION = 1

    @classmethod
    def revision(cls, cached):
        return cached[cls.REVISION_POSITION]

    @classmethod
    def registry(cls, cached):
        return cached[cls.REGISTRY_POSITION]

    @staticmethod
    def create(revision, registry):
        return revision, registry
