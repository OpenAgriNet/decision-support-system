"""Reads the scheme catalog from a tenant-mounted CSV file (issue #34).

CSV rather than YAML, against the repo's own config house style, because this
one file is domain data owned by an agriculture officer rather than an
operator: scheme lists already live in spreadsheets, and one row per scheme
reviews cleanly in a pull request. The columns are
``scheme_code,scheme_name,scheme_aliases``, aliases separated by ``|``.

Nothing ships in the image. A national scheme list is still a tenant's
decision — a Maharashtra deployment and a Bihar one want different subsets and
their own state schemes — so an unmounted catalog is a real state, and one the
loader warns about rather than papering over.

Mirrors ``config/policy_loader.py`` on failure: a configured path that is not
there raises rather than silently falling back to a different configuration.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Mapping
from pathlib import Path

from dss.core.enrichment.models import Scheme
from dss.core.enrichment.normalize import alias_key

logger = logging.getLogger(__name__)

_CODE, _NAME, _ALIASES = "scheme_code", "scheme_name", "scheme_aliases"
_REQUIRED = (_CODE, _NAME, _ALIASES)
_ALIAS_SEPARATOR = "|"

UNSET_CATALOG = (
    "DSS_SCHEMES_CONFIG_PATH is not set, so no scheme catalog is loaded and "
    "scheme enrichment is inert: a query naming a scheme reaches the network "
    "with the farmer's own words rather than the official scheme name. Mount "
    "a catalog CSV to enable it."
)


class MalformedSchemeCatalog(Exception):
    """The catalog file will not load. Raised at boot, never at turn time —
    a tenant's typo should stop a deployment, not degrade one farmer's answer.
    """


class CsvSchemeCatalog:
    """Satisfies `ports.scheme_catalog.SchemeCatalog`. Holds the finished
    index; the file was read once, by `load_scheme_catalog`."""

    def __init__(self, aliases: Mapping[str, Scheme]) -> None:
        self._aliases = dict(aliases)

    def aliases(self) -> Mapping[str, Scheme]:
        return self._aliases


def load_scheme_catalog(path: Path | None) -> CsvSchemeCatalog:
    """Read and index a catalog.

    - ``path`` unset → an empty catalog and one warning (no bundled default).
    - ``path`` set but missing → ``FileNotFoundError``.
    - ``path`` set but broken → ``MalformedSchemeCatalog``, naming the row.
    """

    if path is None:
        logger.warning(UNSET_CATALOG)
        return CsvSchemeCatalog({})

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return CsvSchemeCatalog(_index(csv.DictReader(handle), source=path.name))


def _index(rows: csv.DictReader, *, source: str) -> dict[str, Scheme]:
    aliases: dict[str, Scheme] = {}
    missing = [column for column in _REQUIRED if column not in (rows.fieldnames or ())]
    if missing:
        raise MalformedSchemeCatalog(
            f"{source} is missing the column(s) {', '.join(missing)} — a scheme "
            f"catalog is {','.join(_REQUIRED)}"
        )

    for number, row in enumerate(rows, start=2):  # 2: row 1 is the header
        scheme = _scheme_from(row, source=source, number=number)
        for text in _alias_texts(row, scheme):
            _claim(aliases, alias_key(text), scheme, source=source, number=number)
    return aliases


def _scheme_from(row: dict[str, str], *, source: str, number: int) -> Scheme:
    code = (row.get(_CODE) or "").strip()
    name = (row.get(_NAME) or "").strip()
    if not code or not name:
        raise MalformedSchemeCatalog(
            f"{source} row {number} needs both {_CODE} and {_NAME}; "
            f"got {_CODE}={code!r}, {_NAME}={name!r}"
        )
    return Scheme(code=code, name=name)


def _alias_texts(row: dict[str, str], scheme: Scheme) -> list[str]:
    """The alias column plus the scheme's own name, blanks dropped.

    Blank entries are skipped rather than rejected: a trailing `|` is a
    spreadsheet artefact, not a mistake worth refusing to boot over.
    """

    listed = (row.get(_ALIASES) or "").split(_ALIAS_SEPARATOR)
    return [text for text in (*listed, scheme.name) if text.strip()]


def _claim(
    aliases: dict[str, Scheme],
    key: str,
    scheme: Scheme,
    *,
    source: str,
    number: int,
) -> None:
    """Record one alias, refusing a key two different schemes both claim.

    A repeat within one scheme is harmless — the official name is often also
    listed as an alias — but across schemes it is unresolvable: `find_scheme`
    breaks ties by span length, which says nothing about which scheme was
    meant. That is a different judgement from an alias being *questionable*
    (a bare commodity word like `makhana`, which the catalog's author is
    trusted to keep out); this one has no correct answer at all.
    """

    if not key:
        return
    claimed = aliases.get(key)
    if claimed is not None and claimed != scheme:
        raise MalformedSchemeCatalog(
            f"{source} row {number}: the alias {key!r} is already claimed by "
            f"{claimed.code!r}, so a query using it cannot be resolved to "
            f"either scheme"
        )
    aliases[key] = scheme
