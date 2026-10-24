"""Declaration metadata shared by preference Owners and their form projections."""

from copy import deepcopy
from dataclasses import MISSING, fields
from typing import get_origin

from llm.core.schema import SchemaKeyword, SchemaType


class SettingMetadata:
    REQUIRES_RESTART = "requires_restart"
    LIVE = False
    RESTART = True
    SCHEMA = "schema"
    PATH_SEPARATOR = "."
    JSON_TYPES = {
        str: SchemaType.STRING,
        bool: SchemaType.BOOLEAN,
        int: SchemaType.INTEGER,
        float: SchemaType.NUMBER,
        list: SchemaType.ARRAY,
        dict: SchemaType.OBJECT,
    }

    @classmethod
    def restart(cls, declaration):
        return bool(declaration.metadata.get(cls.REQUIRES_RESTART, cls.LIVE))

    @classmethod
    def describe(cls, model):
        return {
            item.name: {cls.REQUIRES_RESTART: cls.restart(item)}
            for item in fields(model)
        }

    @classmethod
    def properties(cls, model):
        return {
            item.name: {
                SchemaKeyword.TYPE: cls.JSON_TYPES[get_origin(item.type) or item.type],
                cls.REQUIRES_RESTART: cls.restart(item),
                **deepcopy(item.metadata.get(cls.SCHEMA, {})),
            }
            for item in fields(model)
        }

    @staticmethod
    def names_of_type(model, kind):
        return tuple(item.name for item in fields(model) if item.type is kind)

    @staticmethod
    def initial(model, attribute):
        declaration = next(
            item for item in fields(model) if item.name == attribute.__name__
        )
        return (
            deepcopy(declaration.default)
            if declaration.default is not MISSING
            else declaration.default_factory()
        )

    @classmethod
    def path(cls, section, name):
        return section + cls.PATH_SEPARATOR + name

    @classmethod
    def restart_fields(cls, model):
        return tuple(item.name for item in fields(model) if cls.restart(item))
