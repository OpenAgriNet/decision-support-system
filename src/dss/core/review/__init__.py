"""Response review — optional post-hoc grounding/safety/length/tone check.

Does not block the stream: it logs, flags for eval, and may append a correction.
Checks each claim against its own cited source. Optional — when a deployment binds
no reviewer, the orchestrator logs at startup that grounding violations will not
be detected (design v2 §6.9).
"""
