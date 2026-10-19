from datetime import date, datetime, timezone
import hashlib
from math import isfinite
import os

import pandas as pd
import requests
import streamlit as st

from maxtrade.stock_data import INDIA


OPTION_UNDERLYINGS = {'NIFTY 50': 'NSE_INDEX|Nifty 50', 'NIFTY BANK': 'NSE_INDEX|Nifty Bank',
                      'SENSEX': 'BSE_INDEX|SENSEX'}
PUBLIC_UNDERLYINGS = {'NIFTY 50': ('nifty', 'NIFTY', 'NSE'),
                      'NIFTY BANK': ('nifty-bank', 'BANKNIFTY', 'NSE'),
                      'SENSEX': ('sp-bse-sensex', 'SENSEX', 'BSE')}


class OptionChainError(ValueError):
    pass


def access_token():
    token = os.environ.get('UPSTOX_ACCESS_TOKEN')
    if token is None:
        try:
            token = st.secrets.get('UPSTOX_ACCESS_TOKEN', '')
        except FileNotFoundError:
            token = ''
    return str(token or '').strip()


def option_request(endpoint, instrument, token, expiry=None):
    if endpoint not in ('contract', 'chain') or instrument not in OPTION_UNDERLYINGS.values() or not token:
        raise OptionChainError('Upstox market-data access is not configured.')
    params = {'instrument_key': instrument}
    if expiry is not None:
        params['expiry_date'] = expiry
    try:
        response = requests.get(f'https://api.upstox.com/v2/option/{endpoint}', params=params,
                                headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'}, timeout=12)
        if response.status_code in (401, 403):
            raise OptionChainError('Upstox access expired or is not permitted. Update the private access token.')
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        if isinstance(error, OptionChainError):
            raise
        raise OptionChainError('Upstox option data is temporarily unavailable.') from None
    if not isinstance(payload, dict) or payload.get('status') != 'success' or not isinstance(payload.get('data'), list):
        raise OptionChainError('Upstox returned an invalid option-data response.')
    if not payload['data']:
        raise OptionChainError('No option contracts or quotes available.')
    return payload['data']


def number(value, signed=False):
    if value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise OptionChainError('Invalid option quote.') from None
    if isinstance(value, bool) or not isfinite(result) or (not signed and result < 0):
        raise OptionChainError('Invalid option quote.')
    return result


def normalize_option_chain(payload, instrument, expiry):
    rows = []
    strikes = set()
    spots = set()
    contracts = set()
    for item in payload:
        if not isinstance(item, dict) or item.get('underlying_key') != instrument or item.get('expiry') != expiry:
            raise OptionChainError('Option chain identity or expiry mismatch.')
        strike, spot = number(item.get('strike_price')), number(item.get('underlying_spot_price'))
        if strike is None or spot is None or strike <= 0 or spot <= 0 or strike in strikes:
            raise OptionChainError('Invalid or duplicate option strike.')
        strikes.add(strike)
        spots.add(spot)
        row = {'Strike': strike, 'Expiry': expiry, 'Underlying': instrument, 'Spot': spot}
        for side, field in [('CE', 'call_options'), ('PE', 'put_options')]:
            quote = item.get(field)
            if quote is None:
                quote = {}
            if not isinstance(quote, dict):
                raise OptionChainError('Invalid option contract.')
            contract = quote.get('instrument_key')
            if quote and (not isinstance(contract, str) or not contract.startswith(instrument.split('_')[0] + '_FO|')):
                raise OptionChainError('Option contract exchange mismatch.')
            if contract is not None:
                if contract in contracts or not contract.split('|')[-1]:
                    raise OptionChainError('Invalid or duplicate option contract.')
                contracts.add(contract)
            row[f'{side} contract'] = contract
            market, greeks = quote.get('market_data'), quote.get('option_greeks')
            market = {} if market is None else market
            greeks = {} if greeks is None else greeks
            if not isinstance(market, dict) or not isinstance(greeks, dict):
                raise OptionChainError('Invalid option quote fields.')
            for key, label in [('oi', 'OI'), ('volume', 'Volume'), ('ltp', 'LTP'), ('bid_price', 'Bid'), ('ask_price', 'Ask')]:
                row[f'{side} {label}'] = number(market.get(key))
            previous = number(market.get('prev_oi'))
            row[f'{side} OI change'] = row[f'{side} OI'] - previous if row[f'{side} OI'] is not None and previous is not None else None
            for key in ('iv', 'delta', 'gamma', 'theta', 'vega'):
                row[f'{side} {key.upper() if key == "iv" else key.title()}'] = number(greeks.get(key), signed=key in ('delta', 'theta'))
            bid, ask = row[f'{side} Bid'], row[f'{side} Ask']
            if bid is not None and ask is not None and ask > 0 and bid > ask:
                raise OptionChainError('Crossed option bid/ask quote.')
        rows.append(row)
    if not rows or len(spots) != 1:
        raise OptionChainError('Empty or inconsistent option-chain snapshot.')
    return sorted(rows, key=lambda row: row['Strike'])


@st.cache_data(ttl=300, max_entries=12, show_spinner=False)
def load_expiries(instrument, auth_id, _token):
    contracts = option_request('contract', instrument, _token)
    expiries = set()
    for item in contracts:
        if not isinstance(item, dict) or item.get('underlying_key') != instrument:
            raise OptionChainError('Option contract underlying mismatch.')
        contract = item.get('instrument_key')
        if (item.get('instrument_type') not in ('CE', 'PE') or
                not isinstance(contract, str) or
                not contract.startswith(instrument.split('_')[0] + '_FO|') or
                not contract.split('|')[-1]):
            raise OptionChainError('Invalid option contract identity.')
        try:
            expiry = date.fromisoformat(item['expiry'])
        except (KeyError, TypeError, ValueError):
            raise OptionChainError('Invalid option contract expiry.') from None
        if expiry >= datetime.now(INDIA).date():
            expiries.add(expiry.isoformat())
    if not expiries:
        raise OptionChainError('No unexpired option contracts available.')
    return sorted(expiries)


@st.cache_data(ttl=30, max_entries=24, show_spinner=False)
def load_chain(instrument, expiry, auth_id, _token):
    rows = normalize_option_chain(option_request('chain', instrument, _token, expiry), instrument, expiry)
    return rows, datetime.now(timezone.utc)


def normalize_public_chain(payload, title, expiry=None):
    if title not in PUBLIC_UNDERLYINGS or not isinstance(payload, dict):
        raise OptionChainError('Invalid public option-chain response.')
    chain = payload.get('optionChain')
    if not isinstance(chain, dict) or not isinstance(chain.get('optionChains'), list):
        raise OptionChainError('Invalid public option-chain response.')
    details = chain.get('expiryDetailsDto')
    if not isinstance(details, dict) or not isinstance(details.get('expiryDates'), list):
        raise OptionChainError('Public option expiries are unavailable.')
    try:
        current = date.fromisoformat(details['currentExpiry']).isoformat()
        expiries = sorted({date.fromisoformat(value).isoformat() for value in details['expiryDates']})
    except (KeyError, TypeError, ValueError):
        raise OptionChainError('Invalid public option expiry.') from None
    if current not in expiries or (expiry is not None and current != expiry):
        raise OptionChainError('Public option-chain expiry mismatch.')
    _, prefix, exchange = PUBLIC_UNDERLYINGS[title]
    expiry_date = date.fromisoformat(current)
    weekly = f'{prefix}{expiry_date:%y}{"123456789OND"[expiry_date.month - 1]}{expiry_date:%d}'
    monthly = f'{prefix}{expiry_date:%y%b}'.upper()
    rows, strikes, contracts = [], set(), set()
    for item in chain['optionChains']:
        if not isinstance(item, dict):
            raise OptionChainError('Invalid public option row.')
        raw_strike = number(item.get('strikePrice'))
        if raw_strike is None or raw_strike <= 0 or raw_strike in strikes:
            raise OptionChainError('Invalid or duplicate public option strike.')
        strikes.add(raw_strike)
        strike = raw_strike / 100
        row = {'Strike': strike, 'Expiry': current, 'Underlying': OPTION_UNDERLYINGS[title], 'Spot': None}
        for side, field in [('CE', 'callOption'), ('PE', 'putOption')]:
            quote = item.get(field)
            quote = {} if quote is None else quote
            if not isinstance(quote, dict):
                raise OptionChainError('Invalid public option contract.')
            contract = quote.get('growwContractId')
            expected = {f'{weekly}{strike:g}{side}', f'{monthly}{strike:g}{side}'}
            if quote and (not isinstance(contract, str) or contract not in expected or quote.get('exchange') != exchange or
                          number(quote.get('strikePrice')) != raw_strike or contract in contracts):
                raise OptionChainError('Public option contract identity mismatch.')
            if contract is not None:
                contracts.add(contract)
            row[f'{side} contract'] = contract
            for key, label in [('openInterest', 'OI'), ('volume', 'Volume'), ('ltp', 'LTP')]:
                row[f'{side} {label}'] = number(quote.get(key))
            previous = number(quote.get('prevOpenInterest'))
            row[f'{side} OI change'] = row[f'{side} OI'] - previous if row[f'{side} OI'] is not None and previous is not None else None
            for label in ('Bid', 'Ask', 'IV', 'Delta', 'Gamma', 'Theta', 'Vega'):
                row[f'{side} {label}'] = None
            timestamp = number(quote.get('lastTradeTime'))
            try:
                traded = datetime.fromtimestamp(timestamp, timezone.utc) if timestamp else None
            except (OverflowError, OSError, ValueError):
                raise OptionChainError('Invalid public last-trade timestamp.') from None
            if traded is not None and (traded - datetime.now(timezone.utc)).total_seconds() > 60:
                raise OptionChainError('Public last-trade timestamp is in the future.')
            row[f'{side} Last trade UTC'] = traded.isoformat() if traded else None
        if row['CE contract'] is None and row['PE contract'] is None:
            raise OptionChainError('Public option row has no contracts.')
        rows.append(row)
    if not rows:
        raise OptionChainError('No public option quotes available.')
    return sorted(rows, key=lambda row: row['Strike']), expiries, current


@st.cache_data(ttl=30, max_entries=24, show_spinner=False)
def load_public_chain(title):
    if title not in PUBLIC_UNDERLYINGS:
        raise OptionChainError('Unsupported public option index.')
    symbol = PUBLIC_UNDERLYINGS[title][0]
    try:
        response = requests.get(f'https://groww.in/v1/api/option_chain_service/v1/option_chain/{symbol}',
                                headers={'Accept': 'application/json'}, timeout=12)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise OptionChainError('Groww public option data is unavailable. Try again later; no broker token is required.') from None
    rows, expiries, current = normalize_public_chain(payload, title)
    return rows, datetime.now(timezone.utc), expiries, current


def render_stock_option_chain():
    st.subheader('Option chain')
    source = st.selectbox('Source', ['Groww public', 'Upstox'], key='option_source')
    controls = st.columns([2, 2, .3])
    title = controls[0].selectbox('Index', list(OPTION_UNDERLYINGS), key='option_index')
    public = source == 'Groww public'
    token = '' if public else access_token()
    if not public and not token:
        st.info('Upstox market-data access is not configured. UPSTOX_ACCESS_TOKEN must be set privately on the server.')
        return
    instrument = OPTION_UNDERLYINGS[title]
    auth_id = hashlib.sha256(token.encode()).hexdigest()
    if controls[2].button('', icon=':material/refresh:', help='Refresh option chain', key='option_refresh'):
        load_expiries.clear()
        load_chain.clear()
        load_public_chain.clear()
    try:
        if public:
            with st.spinner('Loading public option quotes...'):
                rows, fetched, _, expiry = load_public_chain(title)
            controls[1].selectbox('Expiry', [expiry], key=f'option_public_expiry_{instrument}', disabled=True)
        else:
            with st.spinner('Loading option contracts...'):
                expiries = load_expiries(instrument, auth_id, token)
            expiry = controls[1].selectbox('Expiry', expiries, key=f'option_expiry_{instrument}')
            with st.spinner('Loading option quotes...'):
                rows, fetched = load_chain(instrument, expiry, auth_id, token)
    except OptionChainError as error:
        st.error(str(error))
        return
    if date.fromisoformat(expiry) < datetime.now(INDIA).date() or (datetime.now(timezone.utc) - fetched).total_seconds() > 60:
        st.warning('This snapshot has expired. Refresh the option chain.')
        return
    st.caption(f'{title} | {expiry} | {source} | Premiums INR | Retrieved {fetched.astimezone(INDIA):%d %b %Y %H:%M:%S} IST')
    st.warning('Exchange quote timestamps are unavailable; quote age is unknown. Snapshot research only, no order execution or buy approval.')
    if public:
        st.caption('Public website feed; availability is not guaranteed. Returned expiry only. OI/volume units are provider-reported and unverified. Spot and Greeks unavailable.')
    frame = pd.DataFrame(rows)
    spot = rows[0]['Spot']
    if spot is None:
        default = max(rows, key=lambda row: (row['CE OI'] or 0) + (row['PE OI'] or 0))['Strike']
        strikes = frame['Strike'].tolist()
        nearest = st.selectbox('Centre strike', strikes, index=strikes.index(default),
                               key=f'option_centre_{source}_{instrument}_{expiry}', format_func=lambda value: f'{value:,.2f}')
    else:
        nearest = min(rows, key=lambda row: abs(row['Strike'] - spot))['Strike']
    call_oi, put_oi = frame['CE OI'], frame['PE OI']
    pcr = put_oi.sum() / call_oi.sum() if call_oi.notna().all() and put_oi.notna().all() and call_oi.sum() > 0 else None
    metrics = st.columns(3)
    metrics[0].metric('Underlying spot', f'{spot:,.2f}' if spot is not None else 'N/A')
    metrics[1].metric('ATM strike', f'{nearest:,.0f}' if spot is not None else 'N/A')
    metrics[2].metric('OI PCR (all strikes)', f'{pcr:.2f}' if pcr is not None else 'N/A')
    all_strikes = st.toggle('All strikes', key='option_all_strikes')
    if not all_strikes:
        count = st.slider('Strikes each side of centre' if spot is None else 'Strikes each side of ATM',
                  1, 25, 10, key='option_strike_count')
        centre = next(index for index, row in enumerate(rows) if row['Strike'] == nearest)
        selected = frame.iloc[max(0, centre - count):centre + count + 1]
    else:
        selected = frame
    columns = [f'CE {field}' for field in ('OI', 'OI change', 'Volume', 'IV', 'LTP', 'Bid', 'Ask')]
    columns += ['Strike'] + [f'PE {field}' for field in ('Bid', 'Ask', 'LTP', 'IV', 'Volume', 'OI change', 'OI')]
    if public:
        columns = [f'CE {field}' for field in ('OI', 'OI change', 'Volume', 'LTP')]
        columns += ['Strike'] + [f'PE {field}' for field in ('LTP', 'Volume', 'OI change', 'OI')]
    styled = selected[columns].style.format(precision=2, na_rep='N/A').apply(
        lambda row: ['background-color: #e3f3ee' if row['Strike'] == nearest else '' for column in row], axis=1)
    st.dataframe(styled, width='stretch', hide_index=True, height=550)
    with st.expander('Contracts and last trades' if public else 'Greeks and contracts'):
        fields = ['Strike'] + [f'{side} {field}' for side in ('CE', 'PE') for field in ('contract', 'Delta', 'Gamma', 'Theta', 'Vega')]
        if public:
            fields = ['Strike'] + [f'{side} {field}' for side in ('CE', 'PE') for field in ('contract', 'Last trade UTC')]
        st.dataframe(selected[fields], width='stretch', hide_index=True)
    export = frame.assign(Source=source, Retrieved_UTC=fetched.isoformat(), Quote_age='UNKNOWN', Execution_enabled=False)
    if public:
        export = export.assign(OI_volume_units='PROVIDER_REPORTED_UNVERIFIED')
    st.download_button('Chain CSV', export.to_csv(index=False), f'{title}-{expiry}-option-chain.csv',
                       'text/csv', icon=':material/download:', key='option_chain_download')