"""Intent recognition — converts a query into a structured capability need.

Emits the subject categories, free-text agriculture subjects, and capabilities a
turn is asking for. Classification lives here and runs independently of
moderation (ADR-0003): the two no longer share a context.
"""
