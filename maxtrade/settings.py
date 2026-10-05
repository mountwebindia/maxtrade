import os
from collections.abc import Mapping
from typing import Any


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
