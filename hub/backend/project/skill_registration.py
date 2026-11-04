"""Import through the Skill owner, then stamp its public Markdown with CAS."""

from llm.core.editable_snapshot import EditableSnapshot
from llm.components.skills import SkillRecord
from .management_values import ResourceTimestamps


async def import_skill(data, source):
    # The backend validates/copies the package and owns its layout and manifest.
    # Never rewrite the external source or reach into managed package files.
    identifier = await data.aimport_directory(source)
    snapshot = EditableSnapshot.read(await data.asnapshot(identifier))
    stamped = ResourceTimestamps.skill(snapshot.data)
    if stamped != snapshot.data:
        await data.awrite_markdown(identifier, SkillRecord.document(stamped).text,
                                   expected_revision=snapshot.version)
    return identifier
