from __future__ import annotations

from typing import Any

import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import VENDORS, openai_compat, sarvam, vendor_for

AT = "/scribe/llm"
SARVAM_KEY = "secret://tenants/t_9c21a4be/sarvam/api-key"
GEMINI_KEY = "secret://tenants/t_9c21a4be/gemini/api-key"


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-a-key")


def sarvam_ref(model: str = "sarvam-105b", key: str = SARVAM_KEY, **options: Any) -> ProviderRef:
    return ProviderRef(
        provider="sarvam", model=model, region="ap-south-1", credential_ref=key, options=options
    )


def compat_ref(model: str | None = "a-model", key: str = GEMINI_KEY, **options: Any) -> ProviderRef:
    return ProviderRef(
        provider="openai_compat",
        model=model,
        credential_ref=key,
        options={"endpoint": "google", **options},
    )


@pytest.mark.parametrize(
    ("r", "pointer"),
    [
        (sarvam_ref(model="sarvam-m"), f"{AT}/model"),
        (sarvam_ref(thinking="no"), f"{AT}/options/thinking"),
        (sarvam_ref(key=GEMINI_KEY), f"{AT}/credentialRef"),
        (compat_ref(model=None), f"{AT}/model"),
        (compat_ref(endpoint="nowhere"), f"{AT}/options/endpoint"),
        (compat_ref(key=SARVAM_KEY), f"{AT}/credentialRef"),
    ],
)
def test_an_llm_placed_outside_the_pipeline_locates_its_problems_there(
    r: ProviderRef, pointer: str
) -> None:
    build = VENDORS[r.provider].llm
    assert build is not None
    with pytest.raises(DafterError) as caught:
        build(r, AT)
    assert any(pointer in d for d in caught.value.details), caught.value.details
    assert not any("/agent/pipeline" in d for d in caught.value.details)


def test_an_llm_in_the_pipeline_still_locates_its_problems_in_the_pipeline() -> None:
    with pytest.raises(DafterError) as caught:
        sarvam.build_llm(sarvam_ref(model="sarvam-m"))
    assert any("/agent/pipeline/llm/model" in d for d in caught.value.details)
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(compat_ref(endpoint="nowhere"))
    assert any("/agent/pipeline/llm/options/endpoint" in d for d in caught.value.details)


def test_both_vendors_build_an_llm_placed_outside_the_pipeline() -> None:
    assert sarvam.build_llm(sarvam_ref(), AT) is not None
    assert openai_compat.build_llm(compat_ref(), AT) is not None


def test_the_vendor_lookup_names_the_place_the_document_gives() -> None:
    with pytest.raises(DafterError) as caught:
        vendor_for(None, Stage.LLM, AT)
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert caught.value.details == (f"at '{AT}': required",)
    with pytest.raises(DafterError) as caught:
        vendor_for(ProviderRef(provider="deepgram"), Stage.LLM, AT)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert caught.value.details[0].startswith(f"at '{AT}/provider': registered:")
