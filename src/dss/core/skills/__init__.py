"""Skill discovery — selects the smallest relevant set of permitted skills.

A skill is reasoning guidance injected into the planner prompt ("for pest
questions, ask which crop first"), not something that executes (design v2 §6.3).
This slice ships the contract and a placeholder selector; real routing lands with
the skills config primitive.
"""
