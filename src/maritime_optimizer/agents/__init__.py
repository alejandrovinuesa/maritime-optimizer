"""Agent layer (requires the ``agents`` extra: ``pip install maritime-optimizer[agents]``).

- :mod:`.tools`       deterministic optimizer tools shared by every agent (no model calls)
- :mod:`.mcp_server`  MCP server exposing those tools (``maritime-opt-mcp``)
- :mod:`.assistant`   natural-language Q&A with Claude driving the tools (``maritime-opt ask``)
- :mod:`.report`      compliance report: deterministic figures, narrative by Claude
- :mod:`.regulatory`  CSV update proposals from a new legal text, validated, never auto-applied

The agents read Anthropic credentials from the environment (``ANTHROPIC_API_KEY`` or an
``ant auth login`` profile).
"""
