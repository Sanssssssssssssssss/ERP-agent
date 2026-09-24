"""Provider and Pi-compatible model streaming layer for Pi."""

# ruff: noqa: F401 - this module intentionally defines the public facade

from erp_harness.providers.catalog import BUILTIN_PROVIDER_CATALOG
from erp_harness.providers.anthropic import AnthropicProvider
from erp_harness.providers.env import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    AnthropicConfig,
    OpenAICompatibleConfig,
    RuntimeProviderAuth,
    openai_compatible_config_from_env,
)
from erp_harness.providers.events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from erp_harness.providers.fake import FakeProvider
from erp_harness.providers.google import GoogleGenerativeAIProvider
from erp_harness.providers.mistral import MistralConversationsProvider
from erp_harness.providers.model_limits import ModelLimitsProvider, RuntimeModelLimits
from erp_harness.providers.openai_codex import (
    DEFAULT_OPENAI_CODEX_BASE_URL,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.provider import CancellationToken, ModelProvider

__all__ = [name for name in globals() if not name.startswith("_")]
