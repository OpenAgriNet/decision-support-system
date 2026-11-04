"""Schema packs as data: the files one pack ships and the rules that read them.

Discovery, the planner, forms and the adapters all read packs. None of them
owns the pack, so the readers live here and each of them imports from here.
"""
