"""Intent recognition — converts a query into a structured capability need.

Decomposes a turn into one or more asks, each naming a subject category, an
interaction type (advise / observe / act), and an optional free-text subject, plus
one overall confidence. Classification lives here and runs independently of
moderation (ADR-0003): the two no longer share a context.
"""
