"""Reads the scheme catalog from a tenant-mounted CSV (issue #34, ADR-0021).

Columns: ``scheme_code,scheme_name,scheme_aliases``, aliases ``|``-separated.
``scheme_code`` is the network's scheme code, verbatim and case-exact: a scheme
pack selects its form and its provider by it.
CSV rather than the repo's YAML house style because this is domain data owned
by an agriculture officer, whose scheme lists already live in spreadsheets.
Nothing ships in the image; a missing configured path raises, as in `policy_loader`.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from pathlib import Path

from dss.adapters.scheme_catalog.normalize import alias_key
from dss.core.intent.models import Scheme, SchemeEntry

logger = logging.getLogger(__name__)

_CODE, _NAME, _ALIASES = "scheme_code", "scheme_name", "scheme_aliases"
_REQUIRED = (_CODE, _NAME, _ALIASES)
_ALIAS_SEPARATOR = "|"

UNSET_CATALOG = (
    "DSS_SCHEMES_CONFIG_PATH is not set, so no scheme catalog is loaded: the "
    "intent model is shown no schemes, and no ask names one. Mount a catalog "
    "CSV to enable it."
)


class MalformedSchemeCatalogError(Exception):
    """The catalog file will not load. Raised at boot, never at turn time: a
    tenant's typo should stop a deployment, not degrade one farmer's answer.
    """


class CsvSchemeCatalog:
    """A `SchemeCatalog` over the rows `load_scheme_catalog` read once."""

    def __init__(self, schemes: Sequence[SchemeEntry] = ()) -> None:
        self._schemes = tuple(schemes)

    def schemes(self) -> Sequence[SchemeEntry]:
        return self._schemes


def load_scheme_catalog(path: Path | None) -> CsvSchemeCatalog:
    """Read and index a catalog.

    - ``path`` unset → an empty catalog and one warning (no bundled default).
    - ``path`` set but missing → ``FileNotFoundError``.
    - ``path`` set but broken → ``MalformedSchemeCatalogError``, naming the row.
    """

    if path is None:
        logger.warning(UNSET_CATALOG)
        return CsvSchemeCatalog()

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return _index(csv.DictReader(handle), source=path.name)


def _index(rows: csv.DictReader, *, source: str) -> CsvSchemeCatalog:
    aliases: dict[str, Scheme] = {}
    schemes: list[SchemeEntry] = []
    missing = [column for column in _REQUIRED if column not in (rows.fieldnames or ())]
    if missing:
        raise MalformedSchemeCatalogError(
            f"{source} is missing the column(s) {', '.join(missing)} — a scheme "
            f"catalog is {','.join(_REQUIRED)}"
        )

    for number, row in enumerate(rows, start=2):  # 2: row 1 is the header
        scheme = _scheme_from(row, source=source, number=number)
        texts = _alias_texts(row, scheme)
        for text in texts:
            _claim(aliases, alias_key(text), scheme, source=source, number=number)
        schemes.append(SchemeEntry(scheme=scheme, aliases=_other_names(texts, scheme)))
    return CsvSchemeCatalog(schemes)


def _scheme_from(row: dict[str, str], *, source: str, number: int) -> Scheme:
    code = (row.get(_CODE) or "").strip()
    name = (row.get(_NAME) or "").strip()
    if not code or not name:
        raise MalformedSchemeCatalogError(
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


def _other_names(texts: list[str], scheme: Scheme) -> tuple[str, ...]:
    """The aliases worth showing the intent model: one spelling each, and
    never the official name again."""

    seen = {alias_key(scheme.name)}
    names: list[str] = []
    for text in texts:
        key = alias_key(text)
        if key and key not in seen:
            seen.add(key)
            names.append(text.strip())
    return tuple(names)


def _claim(
    aliases: dict[str, Scheme],
    key: str,
    scheme: Scheme,
    *,
    source: str,
    number: int,
) -> None:
    """Record one alias, refusing a key two different schemes both claim.

    A repeat within one scheme is harmless; across schemes the intent model
    could not tell which one the farmer meant.
    """

    if not key:
        return
    claimed = aliases.get(key)
    if claimed is not None and claimed != scheme:
        raise MalformedSchemeCatalogError(
            f"{source} row {number}: the alias {key!r} is already claimed by "
            f"{claimed.code!r}, so a query using it cannot be resolved to "
            f"either scheme"
        )
    aliases[key] = scheme
