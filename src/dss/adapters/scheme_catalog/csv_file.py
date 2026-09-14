"""Reads the scheme catalog from a tenant-mounted CSV (issue #34).

Columns: ``scheme_code,scheme_name,scheme_aliases``, aliases ``|``-separated.
CSV rather than the repo's YAML house style because this is domain data owned
by an agriculture officer, whose scheme lists already live in spreadsheets.
Nothing ships in the image; a missing configured path raises, as in `policy_loader`.
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
    """The catalog file will not load. Raised at boot, never at turn time: a
    tenant's typo should stop a deployment, not degrade one farmer's answer.
    """


class CsvSchemeCatalog:
    """A `SchemeCatalog` over a finished index, read once by
    `load_scheme_catalog`."""

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

    A trailing `|` is a spreadsheet artefact, not a mistake worth refusing to
    boot over.
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

    A repeat within one scheme is harmless; across schemes it has no correct
    answer, since `find_scheme` breaks ties by span length.
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
