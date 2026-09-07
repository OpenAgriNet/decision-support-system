"""Planning — decides what to actually call, in what order, and what is missing.

Discovery says who *could* answer; the planner turns that into a concrete,
data-only ``Plan`` (no ``eval``, the runtime reads it). It owns sufficiency and
the moderation barrier: the moderation verdict is awaited here, at the last
moment before any side-effecting call (design v2 §3.2, §6.6).
"""
