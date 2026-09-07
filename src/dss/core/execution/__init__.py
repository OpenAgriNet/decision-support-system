"""Plan execution — the only component that touches the outside world.

Reads the plan and calls things: MCP tools and Provider capabilities both go
through here, and the runtime resolves which. No LLM. Provider calls may already
have happened and cannot be taken back — this is the side of the barrier that
cannot be discarded (design v2 §3, §6.7).
"""
