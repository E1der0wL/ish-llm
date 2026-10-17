"""Management metadata stays independent of runtime loading and template data."""

from dataclasses import FrozenInstanceError
import subprocess
import sys
import unittest

from hub.asset.guides import SKILLS, STARTER_SKILLS, SYSTEM_PROMPT
from hub.backend.project.component_commands import component_commands
from hub.components import COMPONENTS, component_spec


class ComponentCatalogTests(unittest.TestCase):
    def test_import_is_lightweight_and_commands_use_exact_component_names(self):
        subprocess.run([sys.executable, "-c", "import sys; import hub.components; "
            "assert 'llm' not in sys.modules; assert 'prompt_toolkit' not in sys.modules"], check=True)
        commands = component_commands(tuple(spec.name for spec in COMPONENTS))
        self.assertEqual(list(commands), ["tools", "skills", "mcp", "rag", "agents", "workflows", "prompts", "memory"])
        self.assertTrue(all(command == name for command, name in commands.items()))
        self.assertEqual(component_commands(("memory", "goals", "refinement", "skills")),
                         {"skills": "skills", "memory": "memory"})
        with self.assertRaises(ValueError):
            component_spec("unregistered")
        with self.assertRaises(FrozenInstanceError):
            component_spec("tools").name = "changed"

    def test_starter_guides_have_unique_ids_and_load_complete_instructions(self):
        self.assertEqual(len({skill.identifier for skill in STARTER_SKILLS}), 7)
        self.assertEqual(tuple(skill.load() for skill in STARTER_SKILLS), SKILLS)
        expected = {"hub-code-change": "test_workspace", "hub-diagnose": "reproduction",
            "hub-document": "audience", "hub-create-rag": "rag_add_document",
            "hub-create-workflow": "workflow_schema", "hub-create-skill": "skill_create",
            "hub-create-agent": "agent_create"}
        for identifier, title, description, instructions in SKILLS:
            self.assertIn(expected[identifier], instructions)
            self.assertNotRegex(title + description + instructions, "[가-힣]")
        self.assertIn("current Run", SYSTEM_PROMPT)
        self.assertNotRegex(SYSTEM_PROMPT, "[가-힣]")
        self.assertIn("Respond in the user's language", SYSTEM_PROMPT)
