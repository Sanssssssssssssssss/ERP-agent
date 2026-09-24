"""Explicit no-timeout policy must survive configuration and actual adapters."""
import asyncio
from unittest.mock import patch

from bench.adapters.harbor_agent import deadline_command
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.config import OpenAICompatibleProviderConfig, ProviderSettings, provider_settings_from_json


def test_explicit_no_timeout_roundtrips_and_reaches_http_client():
    config = OpenAICompatibleProviderConfig(name="test", timeout_seconds=None)
    settings = ProviderSettings(providers=(config,))
    restored = provider_settings_from_json({"default_provider": "test", "providers": [config.to_json()]})
    assert restored.providers[0].timeout_seconds is None
    with patch("erp_harness.providers.config._effective_provider_configs", return_value=(config,)):
        assert provider_settings_from_json(settings.to_json()).providers[0].timeout_seconds is None
    async def check():
        provider = OpenAICompatibleProvider(OpenAICompatibleConfig(api_key="test-only", timeout_seconds=None))
        try:
            timeout = provider._get_client().timeout
            assert timeout.connect is timeout.read is timeout.write is timeout.pool is None
        finally:
            await provider.aclose()
    asyncio.run(check())
    assert deadline_command("true", None) == "bash -c true"
    assert "timeout --signal=TERM" in deadline_command("true", 10)
