"""Intent recognition — converts a query into a structured capability need.

Emits domain, subdomain, entities, and action type. Classification lives here,
not in moderation: moderation consumes the intent object and judges harm only.
"""
