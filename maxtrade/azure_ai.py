from __future__ import annotations

import json
from typing import Any

import requests

from maxtrade.settings import AzureOpenAIConfig
from maxtrade.ai_review import (ProviderUnavailable, REVIEW_PROMPT, REVIEW_SCHEMA,
                                encoded_evidence, token_usage, validate_review)


def review_evidence(config: AzureOpenAIConfig, report: dict[str, Any]) -> dict[str, Any]:
    encoded = encoded_evidence(report)
    if len(encoded) > 60000:
        raise ValueError('Evidence exceeds Azure review size limit')
    responses = config.endpoint.endswith('/openai/v1/responses')
    messages = [
        {'role': 'system', 'content': REVIEW_PROMPT},
        {'role': 'user', 'content': encoded}]
    if responses:
        url = config.endpoint
        parameters = {}
        body = {'model': config.deployment, 'input': messages, 'store': False,
                'max_output_tokens': 4096, 'text': {'format': {'type': 'json_schema',
                'name': 'evidence_review', 'strict': True, 'schema': REVIEW_SCHEMA}}}
    else:
        url = f'{config.endpoint}/openai/deployments/{config.deployment}/chat/completions'
        parameters = {'api-version': config.api_version}
        body = {'messages': messages, 'response_format': {'type': 'json_object'}, 'max_tokens': 600}
    try:
        with requests.Session() as session:
            response = session.post(
                url, params=parameters, headers={'api-key': config.api_key},
                json=body, timeout=30,
                allow_redirects=False)
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise ProviderUnavailable(f'Azure unavailable (HTTP {response.status_code})')
            if response.status_code != 200:
                raise ValueError(f'Azure request failed (HTTP {response.status_code}); verify deployment, version and credentials privately.')
            if len(response.content) > 100000:
                raise ValueError('Azure response exceeds size limit')
            document = response.json()
            if responses:
                if (document.get('status') != 'completed' or document.get('error')
                        or document.get('incomplete_details')
                        or any(item.get('blocked') for item in document.get('content_filters', []))):
                    raise ValueError('Azure response incomplete or blocked; paper entries blocked.')
                parts = [part for item in document['output'] if item['type'] == 'message'
                         for part in item['content']]
                if any(part['type'] == 'refusal' for part in parts):
                    raise ValueError('Azure refused the review; paper entries blocked.')
                result = json.loads(''.join(part['text'] for part in parts if part['type'] == 'output_text'))
            else:
                choice = document['choices'][0]
                if choice.get('finish_reason', 'stop') != 'stop' or choice['message'].get('refusal'):
                    raise ValueError('Azure review incomplete or refused; paper entries blocked.')
                result = json.loads(choice['message']['content'])
    except requests.exceptions.SSLError as error:
        raise ValueError('Azure TLS verification failed; paper entries blocked.') from error
    except requests.Timeout as error:
        raise ProviderUnavailable('Azure review timed out; paper entries blocked.') from error
    except requests.ConnectionError as error:
        raise ProviderUnavailable('Azure network request failed; paper entries blocked.') from error
    except requests.RequestException as error:
        raise ValueError('Azure network request failed; paper entries blocked.') from error
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('Azure response malformed; paper entries blocked.') from error
    result = validate_review(result)
    result.update(agent='azure-evidence-review-v1', deployment=config.deployment, usage=token_usage(document))
    return result


def render_azure_settings() -> None:
    import sqlite3
    import streamlit as st
    from maxtrade.paper import PaperLedger
    from maxtrade.settings import AZURE_DEFAULT_DEPLOYMENT, AZURE_DEFAULT_ENDPOINT, azure_openai_config

    st.markdown('#### AI research mode')
    try:
        secrets = st.secrets.to_dict()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    try:
        config = azure_openai_config(secrets=secrets)
    except ValueError as error:
        st.warning(str(error))
        config = None
    st.info('Azure OpenAI configured · connection unverified' if config else 'Azure OpenAI not configured')
    from maxtrade.settings import claude_config
    from maxtrade.ai_review import review_claude
    try:
        claude = claude_config(secrets=secrets)
        st.info('Claude configured · connection unverified' if claude else 'Claude not configured')
        if config:
            st.caption(f'Claude fallback: {config.fallback_enabled} · PAPER approval enabled: {config.fallback_paper_enabled}')
        if st.button('Test Claude connection', icon=':material/network_check:',
                     disabled=claude is None, key='claude_test') and claude is not None:
            with st.spinner('Checking Claude model...'):
                review_claude(claude, {'product': 'CONNECTION TEST', 'symbol': 'NONE', 'evidence': []})
                st.success('Claude structured connection verified. No trade submitted.')
    except ValueError as error:
        st.warning(str(error))
    if config:
        st.caption(f'Deployment: {config.deployment} · API version: {config.api_version}')
        st.code(f'AZURE_OPENAI_ENDPOINT = "{AZURE_DEFAULT_ENDPOINT}"\n'
            f'AZURE_OPENAI_DEPLOYMENT = "{AZURE_DEFAULT_DEPLOYMENT}"\n'
            'AZURE_OPENAI_API_KEY = "YOUR-PRIVATE-KEY"', language='toml')
        st.caption('Endpoint and deployment above are defaults. Add the key privately in Streamlit Cloud Secrets or the backend environment. Responses v1 needs no dated API version. API keys are never displayed. Connection tests use a paid Azure request; research sends public market evidence to Azure.')
    try:
        ledger = PaperLedger()
        from maxtrade.settings import gemini_shadow_config, load_gemini_settings, save_gemini_settings
        st.markdown('#### Gemini shadow review')
        try:
            private_gemini = load_gemini_settings()
            gemini = gemini_shadow_config(secrets={**secrets, **private_gemini})
            st.info('Gemini shadow configured · connection unverified' if gemini else 'Gemini shadow not configured')
        except ValueError as error:
            st.warning(str(error))
            gemini = None
        if st.session_state.pop('gemini_clear_key', False):
            st.session_state['gemini_api_key'] = ''
        with st.form('gemini_credentials'):
            model = st.text_input('Gemini model', value=getattr(gemini, 'model', 'gemini-2.5-flash'),
                                  key='gemini_model')
            api_key = st.text_input('Gemini API key', type='password', key='gemini_api_key')
            save_credentials = st.form_submit_button('Save Gemini credentials', icon=':material/save:')
        if save_credentials:
            try:
                saved_key = api_key or getattr(gemini, 'api_key', '')
                save_gemini_settings(model, saved_key)
                st.session_state['gemini_clear_key'] = True
                st.session_state['gemini_saved'] = True
                st.rerun()
            except (OSError, ValueError):
                st.error('Gemini settings not saved. Check the model, API key and private storage access.')
        if st.session_state.pop('gemini_saved', False):
            st.success('Gemini credentials saved. Connection unverified.')
        if st.button('Test Gemini connection', icon=':material/network_check:',
                     disabled=gemini is None, key='gemini_test') and gemini is not None:
            from maxtrade.gemini_ai import review_shadow
            try:
                with st.spinner('Checking Gemini model...'):
                    review_shadow(gemini, encoded_evidence({'product': 'CONNECTION TEST', 'symbol': 'NONE', 'evidence': []}))
                st.success('Gemini structured connection verified. No trade submitted.')
            except ValueError:
                st.error('Gemini connection failed. Check key access, model and quota.')
        shadow_enabled = st.toggle('Gemini shadow review', value=ledger.gemini_shadow_enabled(),
                                   key='gemini_shadow_enabled')
        if st.button('Save Gemini shadow option', icon=':material/save:', key='gemini_shadow_save'):
            if shadow_enabled and gemini is None:
                st.warning('Configure Gemini shadow privately before enabling it.')
            else:
                ledger.set_gemini_shadow(shadow_enabled)
                st.success('Gemini shadow option saved.')
        modes = ['Deterministic', 'Azure-assisted']
        mode = st.radio('Research mode', modes, index=modes.index(ledger.ai_mode()), horizontal=True,
                key='ai_mode', width='stretch')
        if st.button('Save AI mode', icon=':material/save:', key='ai_mode_save'):
            if mode == 'Azure-assisted' and config is None:
                st.warning('Configure Azure secrets before selecting Azure-assisted mode.')
            else:
                ledger.set_ai_mode(mode)
                st.success('AI mode saved for paper research cycles.')
        if st.button('Test Azure connection', icon=':material/network_check:', disabled=config is None, key='azure_test'):
            with st.spinner('Checking Azure deployment...'):
                if config is not None:
                    review_evidence(config, {'product': 'CONNECTION TEST', 'symbol': 'NONE', 'evidence': []})
                    st.success('Azure connection and structured response verified. No trade was submitted.')
    except (OSError, sqlite3.Error, ValueError) as error:
        st.warning(str(error))