"""Tier 5 — does a real model fill `Intent.place_name`?

Structural only: the assertion is that whatever the model extracted *resolves*
against the shipped district index, never that it produced a particular string.

The prompt asks for the place in English, so the Marathi query is the case that
matters — the index carries English names only, and a model that echoes "पुणे"
verbatim would leave every non-English turn without a spatial filter while the
English one passed.

Skipped unless `.env` carries a model key, like `tests/e2e/test_onion_price.py`:
the suite stays green on a checkout with no credentials, and this runs
deliberately against whatever `DSS_INTENT_MODEL` the deployment binds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.adapters.area_lookup.csv_lookup import CsvAreaLookup
from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.settings import DEFAULT_DISTRICT_CSV, Settings
from dss.core.intent.service import classify_intent
from dss.core.shared.models import UserTurn
from dss.entrypoint.composition import _resolve_model

_DOTENV = Path(__file__).parents[3] / ".env"


def _dotenv_values() -> dict[str, str]:
    """`.env`, read *without* touching `os.environ`.

    Importing this module must not reconfigure the rest of the run: a
    module-level `load_dotenv()` leaks the developer's real settings into every
    later `Settings()` in the same pytest process, which silently broke the
    defaults tests in `tests/unit/config/`.
    """

    return {name: value for name, value in dotenv_values(_DOTENV).items() if value}


pytestmark = pytest.mark.skipif(
    not (_dotenv_values().keys() & {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"}),
    reason="live model test — needs OPENAI_API_KEY or AZURE_OPENAI_API_KEY in .env",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` into the environment for the duration of one test.

    `_resolve_model` and the Azure SDK read AZURE_OPENAI_* from `os.environ`
    directly, so it has to be there — `monkeypatch` puts it back afterwards.
    """

    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


# The real shipped index, not a fixture: the point is that a model's output
# lands on one of these 784 districts.
LOOKUP = CsvAreaLookup.load(DEFAULT_DISTRICT_CSV)


def _live_llm() -> PydanticAILLMProvider:
    # Plain `Settings()` so .env applies: the deployment's DSS_INTENT_MODEL
    # decides the provider (`azure:...` or `openai:...`). Constructing it with
    # overrides would fall back to the field default and call OpenAI with
    # whatever key happens to be set.
    settings = Settings()
    return PydanticAILLMProvider(
        _resolve_model(settings.intent_model),
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _turn(query: str, *, source_lang: str = "en") -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang=source_lang,
        target_lang=source_lang,
        channel="web",
    )


@pytest.mark.parametrize(
    ("query", "source_lang"),
    [
        ("I am from Pune, what is the wheat price?", "en"),
        # Transliteration, not translation: "from Pune" in Marathi.
        ("मी पुण्याहून आहे, गव्हाचा भाव काय आहे?", "mr"),
    ],
    ids=["english", "marathi"],
)
async def test_a_named_place_resolves_to_one_district(
    query: str, source_lang: str
) -> None:
    intent = await classify_intent(_turn(query, source_lang=source_lang), _live_llm())

    assert intent.place_name is not None, "the model named no place"
    # Exactly one: a name resolving to none would leave the turn without a
    # spatial filter, and one resolving to several is the ambiguous case.
    matches = LOOKUP.resolve(intent.place_name)
    assert len(matches) == 1, f"{intent.place_name!r} resolved to {len(matches)}"


async def test_no_place_named_leaves_it_none() -> None:
    """The prompt says not to guess a place from the crop, the language, or the
    subject. Untested, that instruction is only a hope — a model that invents
    "Pune" here would send every advisory turn to one arbitrary district.
    """

    intent = await classify_intent(_turn("How do I treat potato blight?"), _live_llm())

    assert intent.place_name is None, f"invented {intent.place_name!r}"
