"""Hub's session grouping metadata; backend agent_ids remain membership authority."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4


class ParticipantAction(StrEnum):
    ADD_GROUP = "add_group"
    DELETE_GROUP = "delete_group"
    MOVE = "move"
    EXPEL = "expel"


@dataclass(frozen=True, slots=True)
class ParticipantGroup:
    id: str
    title: str
    members: tuple[str, ...]


class ParticipantGroups:
    KEY = "hub_participant_groups"
    PREFIX = "group:"
    TITLE = "title"
    MEMBERS = "members"

    @classmethod
    def read(cls, metadata, participants):
        values = metadata.get(cls.KEY, {})
        if not isinstance(values, dict):
            raise ValueError("Hub participant groups must be an object")
        result, assigned = [], set()
        for identifier, value in values.items():
            if (not isinstance(identifier, str) or not identifier.startswith(cls.PREFIX)
                    or not isinstance(value, dict) or set(value) != {cls.TITLE, cls.MEMBERS}
                    or not isinstance(value[cls.TITLE], str) or not value[cls.TITLE].strip()
                    or not isinstance(value[cls.MEMBERS], list)
                    or any(not isinstance(member, str) for member in value[cls.MEMBERS])):
                raise ValueError("Invalid Hub participant group")
            members = tuple(value[cls.MEMBERS])
            if len(set(members)) != len(members) or assigned.intersection(members):
                raise ValueError("An Agent can belong to only one Hub group")
            assigned.update(members)
            # Backend membership can be edited outside Hub; never invite from metadata.
            result.append(ParticipantGroup(identifier, value[cls.TITLE],
                                           tuple(member for member in members if member in participants)))
        return tuple(result)

    @classmethod
    def change(cls, metadata, participants, action, identifier, value):
        groups = {group.id: {cls.TITLE: group.title, cls.MEMBERS: list(group.members)}
                  for group in cls.read(metadata, participants)}
        participants = list(participants)
        action = ParticipantAction(action)
        if action == ParticipantAction.ADD_GROUP:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("A group name is required")
            groups[cls.PREFIX + uuid4().hex] = {cls.TITLE: value.strip(), cls.MEMBERS: []}
        elif action == ParticipantAction.DELETE_GROUP:
            del groups[identifier]
        else:
            if identifier not in participants:
                raise ValueError("Agent is not invited to this Session")
            if action == ParticipantAction.MOVE and value and value not in groups:
                raise ValueError("Group no longer exists")
            for group in groups.values():
                group[cls.MEMBERS] = [member for member in group[cls.MEMBERS] if member != identifier]
            if action == ParticipantAction.MOVE and value:
                groups[value][cls.MEMBERS].append(identifier)
            elif action == ParticipantAction.EXPEL:
                participants.remove(identifier)
        return {**metadata, cls.KEY: groups}, participants
