import os
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    deployment: str
    api_version: str
    api_key: str = field(repr=False)


def azure_openai_config(environment: Mapping[str, Any] | None = None,
                        secrets: Mapping[str, Any] | None = None) -> AzureOpenAIConfig | None:
    environment = os.environ if environment is None else environment
    secrets = {} if secrets is None else secrets
    names = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_API_KEY")
    values = [str(environment.get(name) or secrets.get(name) or "").strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError("Azure OpenAI configuration is incomplete; all four settings are required.")
    endpoint, deployment, api_version, api_key = values
    address = urlsplit(endpoint)
    if (address.scheme != "https" or not address.hostname or address.username or address.password
            or address.path not in ("", "/") or address.query or address.fragment):
        raise ValueError("Azure OpenAI endpoint must be an HTTPS resource URL without a path or credentials.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", deployment):
        raise ValueError("Azure OpenAI deployment name is invalid.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", api_version):
        raise ValueError("Azure OpenAI API version must be a dated version, optionally ending in -preview.")
    return AzureOpenAIConfig(endpoint.rstrip("/"), deployment, api_version, api_key)


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
