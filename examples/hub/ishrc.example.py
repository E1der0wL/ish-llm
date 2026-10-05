"""Add this snippet to .ishrc.py after deploying hub/ to plugin/script/hub/."""

hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub_preview = hub_plugin.install(prompt, preview=True)
