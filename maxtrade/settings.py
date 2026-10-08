import os
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import urlsplit


AZURE_DEFAULT_ENDPOINT = "https://neilbisht.services.ai.azure.com/openai/v1/responses"
AZURE_DEFAULT_DEPLOYMENT = "gpt-6-astra-2"


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(repr=False)
    chat_id: str


def telegram_config(environment: Mapping[str, Any] | None = None,
                    secrets: Mapping[str, Any] | None = None) -> TelegramConfig | None:
    environment = os.environ if environment is None else environment
    secrets = {} if secrets is None else secrets
    token, chat_id = [str(environment.get(name) or secrets.get(name) or '').strip()
                      for name in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')]
    if not token and not chat_id:
        return None
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'-?\d+', chat_id):
        raise ValueError('Telegram configuration is incomplete or invalid; add bot token and numeric chat ID privately.')
    return TelegramConfig(token, chat_id)


@dataclass(frozen=True)
class ClaudeConfig:
    model: str
    api_key: str = field(repr=False)


def claude_config(environment: Mapping[str, Any] | None = None,
                  secrets: Mapping[str, Any] | None = None) -> ClaudeConfig | None:
    environment = os.environ if environment is None else environment
    secrets = {} if secrets is None else secrets
    model, api_key = [str(environment.get(name) or secrets.get(name) or '').strip()
                      for name in ('ANTHROPIC_MODEL', 'ANTHROPIC_API_KEY')]
    if not model and not api_key:
        return None
    if not api_key or not re.fullmatch(r'claude-[A-Za-z0-9_.-]+', model):
        raise ValueError('Claude configuration incomplete; add model and API key privately.')
    return ClaudeConfig(model, api_key)


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    deployment: str
    api_version: str
    api_key: str = field(repr=False)
    claude: ClaudeConfig | None = None
    fallback_enabled: bool = False
    fallback_paper_enabled: bool = False


def azure_openai_config(environment: Mapping[str, Any] | None = None,
                        secrets: Mapping[str, Any] | None = None) -> AzureOpenAIConfig | None:
    environment = os.environ if environment is None else environment
    secrets = {} if secrets is None else secrets
    names = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_API_KEY")
    values = [str(environment.get(name) or secrets.get(name) or "").strip() for name in names]
    if not any(values):
        return None
    endpoint, deployment, api_version, api_key = values
    endpoint = endpoint or AZURE_DEFAULT_ENDPOINT
    deployment = deployment or AZURE_DEFAULT_DEPLOYMENT
    address = urlsplit(endpoint)
    responses = address.path.rstrip("/") == "/openai/v1/responses"
    if (address.scheme != "https" or not address.hostname or address.username or address.password
            or (address.path not in ("", "/") and not responses) or address.query or address.fragment):
        raise ValueError("Azure OpenAI endpoint must be an HTTPS resource URL or /openai/v1/responses URL without credentials or query parameters.")
    if not api_key:
        raise ValueError("Azure OpenAI configuration is incomplete; add AZURE_OPENAI_API_KEY privately.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", deployment):
        raise ValueError("Azure OpenAI deployment name is invalid.")
    if responses:
        if api_version not in ("", "v1"):
            raise ValueError("Responses v1 does not use a dated API version; remove AZURE_OPENAI_API_VERSION or set it to v1.")
        api_version = "v1"
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", api_version):
        raise ValueError("Azure OpenAI API version must be a dated version, optionally ending in -preview.")
    flags = []
    for name in ('MAXTRADE_CLAUDE_FALLBACK', 'MAXTRADE_CLAUDE_PAPER_ENABLED'):
        value = str(environment.get(name) or secrets.get(name) or 'false').strip().lower()
        if value not in {'true', 'false', '1', '0'}:
            raise ValueError(f'{name} must be true or false')
        flags.append(value in {'true', '1'})
    claude = claude_config(environment, secrets)
    if flags[0] and claude is None:
        raise ValueError('Claude fallback enabled without private Claude configuration')
    return AzureOpenAIConfig(endpoint.rstrip("/"), deployment, api_version, api_key,
                             claude, flags[0], flags[1])


def credential_status(environment: Mapping[str, Any] | None = None,
                      secrets: Mapping[str, Any] | None = None) -> str:
    """Report presence only. Does not expose credentials or authenticate with the exchange."""
    environment = os.environ if environment is None else environment
    secrets = {} if secrets is None else secrets
    configured = [bool(str(environment.get(name) or secrets.get(name) or "").strip())
                  for name in ("COINDCX_API_KEY", "COINDCX_API_SECRET")]
    if all(configured):
        return "Configured · not connected"
    if any(configured):
        return "Incomplete configuration"
    return "Not configured"
