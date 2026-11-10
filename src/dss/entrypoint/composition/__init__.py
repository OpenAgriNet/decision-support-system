"""The composition root: the one package that names every concrete type.
`root` wires the runner. `schema_packs` reads the packs at boot."""

from dss.entrypoint.composition.root import (
    build_area_lookup,
    build_runner,
    build_runner_with_lifecycle,
)

__all__ = ["build_area_lookup", "build_runner", "build_runner_with_lifecycle"]
