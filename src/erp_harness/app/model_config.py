"""Model and selector settings shared by desktop host and workers."""
import os

from erp_harness.providers.config import OpenAICompatibleProviderConfig, ProviderModelMetadata
from erp_harness.providers.env import OpenAICompatibleConfig

CONTEXT_WINDOW = 128_000
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}


def transport_config(api_key, base_url, provider_name, thinking, receipts, *, max_tokens=None):
    return OpenAICompatibleConfig(
        api_key=api_key, base_url=base_url, reasoning_effort=thinking,
        thinking_format="openai", compat=MODEL_COMPAT, provider_name=provider_name,
        timeout_seconds=None, max_retries=0, max_tokens=max_tokens,
        infer_api_from_model=False, provider_hooks=receipts,
    )


def provider_config(base_url, model, provider_name, thinking):
    return OpenAICompatibleProviderConfig(
        name=provider_name, base_url=base_url, api_key_env="LLM_API_KEY",
        models=(model,), default_model=model, context_windows={model: CONTEXT_WINDOW},
        compat=MODEL_COMPAT,
        model_metadata={model: ProviderModelMetadata(reasoning=True, context_window=CONTEXT_WINDOW)},
        timeout_seconds=None, max_retries=0, thinking_levels=(thinking,),
        thinking_models=(model,), thinking_default=thinking,
        thinking_parameter="reasoning_effort", thinking_defaults={model: thinking},
    )


def capability_router_config():
    """Keep the historical environment alias at one boundary."""
    return os.environ.get("ERP_CAPABILITY_ROUTER_CONFIG") or os.environ.get("ERP_OPENJEV_CONFIG")
