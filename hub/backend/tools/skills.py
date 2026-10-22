"""Keep immutable skill snapshot queries off the backend observation loop."""

import asyncio
from dataclasses import replace
from llm.components.skills import SkillComponent, SkillRecord, SkillName
from llm.components.tools import ToolRegistry


class HubSkillComponent(SkillComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability == "tools":
            from .resources import authoring_tools
            for tool in authoring_tools(data_factory(self.name), self.name, self.schema, discover=False, source_field=SkillRecord.SOURCE, identifier_schema=SkillName.SCHEMA):
                registry.register(tool)
        return registry

    def resolve(self, project, capability):
        value = super().resolve(project, capability)
        if capability != "tools":
            return value

        def off_loop(handler):
            async def query(arguments):
                # These built-in handlers only inspect their immutable Run
                # snapshot. Never adapt arbitrary async tools, live resources,
                # approvals or handlers with side effects this way.
                def read():
                    return asyncio.run(handler(arguments))
                return await asyncio.to_thread(read)
            return query

        return ToolRegistry(tuple(replace(value.get(name), handler=off_loop(value.get(name).handler))
                                  if name in ("skill_list", "skill_read") else value.get(name)
                                  for name in value.names()))
