from __future__ import annotations

import json
from time import monotonic
from typing import Any

import requests

from maxtrade.ai_review import REVIEW_PROMPT, REVIEW_SCHEMA, validate_review
from maxtrade.settings import GeminiShadowConfig


def review_shadow(config: GeminiShadowConfig, evidence: str) -> dict[str, Any]:
    if len(evidence) > 60000:
        raise ValueError('Gemini evidence exceeds size limit')
    body = {'systemInstruction': {'parts': [{'text': REVIEW_PROMPT}]},
            'contents': [{'role': 'user', 'parts': [{'text': evidence}]}],
            'generationConfig': {'maxOutputTokens': 4096,
                                 'responseMimeType': 'application/json',
                                 'responseJsonSchema': REVIEW_SCHEMA}}
    started = monotonic()
    try:
        with requests.Session() as session:
            response = session.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{config.model}:generateContent',
                headers={'x-goog-api-key': config.api_key}, json=body,
                timeout=30, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError(f'Gemini shadow unavailable (HTTP {response.status_code})')
            if len(response.content) > 100000:
                raise ValueError('Gemini response exceeds size limit')
            document = response.json()
            candidates = document['candidates']
            if document.get('promptFeedback', {}).get('blockReason') or len(candidates) != 1:
                raise ValueError('Gemini shadow blocked or ambiguous')
            candidate = candidates[0]
            if candidate['finishReason'] != 'STOP':
                raise ValueError('Gemini shadow incomplete or refused')
            parts = candidate['content']['parts']
            if not parts or any(set(part) != {'text'} or not isinstance(part['text'], str)
                                for part in parts):
                raise ValueError('Gemini shadow contains unexpected content')
            result = validate_review(json.loads(''.join(part['text'] for part in parts)))
            usage = document.get('usageMetadata', {})
            if not isinstance(usage, dict):
                raise ValueError('Gemini usage malformed')
    except requests.RequestException:
        raise ValueError('Gemini shadow network unavailable') from None
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('Gemini shadow response malformed') from None
    result.update(provider='Gemini', model=config.model, status='AVAILABLE',
                  shadow_only=True, execution_enabled=False,
                  latency_ms=round((monotonic() - started) * 1000),
                  usage={name: value for name, value in usage.items()
                         if name in {'promptTokenCount', 'candidatesTokenCount', 'totalTokenCount',
                                     'thoughtsTokenCount'}
                         and isinstance(value, int) and not isinstance(value, bool) and value >= 0})
    return result