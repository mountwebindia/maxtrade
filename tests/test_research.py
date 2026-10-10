import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import Mock, patch

from maxtrade.research import fetch_sentiment, market_evidence, run_coordinated_research, run_market_research, sentiment_evidence
from maxtrade.history import ScanHistory
from maxtrade.research_sources import derivatives_evidence, news_evidence
from maxtrade.paper import PaperLedger, coordinate


class ResearchTests(unittest.TestCase):
    def test_provider_private_config_and_transient_classification(self):
        from requests import Timeout, ConnectionError
        from requests.exceptions import SSLError
        from maxtrade.ai_review import ProviderUnavailable
        from maxtrade.azure_ai import review_evidence
        from maxtrade.settings import azure_openai_config
        values = {'AZURE_OPENAI_API_KEY': 'private-azure', 'ANTHROPIC_API_KEY': 'private-claude',
                  'ANTHROPIC_MODEL': 'claude-test', 'MAXTRADE_CLAUDE_FALLBACK': 'true'}
        config = azure_openai_config(values, {})
        self.assertTrue(config.fallback_enabled)
        self.assertFalse(config.fallback_paper_enabled)
        self.assertNotIn('private-claude', repr(config))
        with self.assertRaises(ValueError):
            azure_openai_config(dict(values, ANTHROPIC_MODEL=''), {})
        for error in (Timeout('private-request'), ConnectionError('private-request')):
            with patch('maxtrade.azure_ai.requests.Session') as session:
                session.return_value.__enter__.return_value.post.side_effect = error
                with self.assertRaises(ProviderUnavailable) as raised:
                    review_evidence(config, {})
                self.assertNotIn('private-request', str(raised.exception))
        for http_code in (401, 403, 429, 500, 503):
            with patch('maxtrade.azure_ai.requests.Session') as session:
                session.return_value.__enter__.return_value.post.return_value.status_code = http_code
                with self.assertRaises(ValueError) as raised:
                    review_evidence(config, {})
                self.assertEqual(isinstance(raised.exception, ProviderUnavailable), http_code >= 429)
        with patch('maxtrade.azure_ai.requests.Session') as session:
            session.return_value.__enter__.return_value.post.side_effect = SSLError('private-request')
            with self.assertRaises(ValueError) as raised:
                review_evidence(config, {})
            self.assertNotIsInstance(raised.exception, ProviderUnavailable)

    def test_claude_structured_review_and_refusal_fail_closed(self):
        import json
        from maxtrade.ai_review import ProviderUnavailable, review_claude
        from maxtrade.settings import ClaudeConfig
        config = ClaudeConfig('claude-test', 'private-test')
        document = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(
            {'verdict': 'CLEAR', 'summary': 'Evidence reviewed', 'concerns': []})}]}
        with patch('maxtrade.ai_review.requests.Session') as session:
            post = session.return_value.__enter__.return_value.post
            response = post.return_value
            response.status_code = 200
            response.content = b'{}'
            response.json.return_value = document
            self.assertEqual(review_claude(config, {})['verdict'], 'CLEAR')
            self.assertEqual(post.call_args.args[0], 'https://api.anthropic.com/v1/messages')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertNotIn('private-test', str(post.call_args.kwargs['json']))
            for invalid in (dict(document, stop_reason='max_tokens'),
                            dict(document, stop_reason='refusal'),
                            dict(document, stop_details={'type': 'refusal'}),
                            dict(document, content=[]),
                            dict(document, content=[{'type': 'text', 'text': '{}'}])):
                response.json.return_value = invalid
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    review_claude(config, {})
            for http_code in (401, 429, 500):
                response.status_code = http_code
                with self.assertRaises(ValueError) as failure:
                    review_claude(config, {})
                self.assertEqual(isinstance(failure.exception, ProviderUnavailable), http_code != 401)

    def test_fallback_shadow_cannot_approve_paper(self):
        report = dict(self.paper_report(), ai_mode='Azure-assisted',
                      ai_review={'verdict': 'CLEAR', 'concerns': [], 'shadow_only': True})
        account = {'capital': 10000, 'equity': 10000, 'daily_pnl': 0,
                   'occupied': False, 'kill_switch': False}
        self.assertFalse(coordinate(report, self.now, account=account, autonomous=True)['approved'])

    def test_quality_uses_completed_volume_bars(self):
        from unittest.mock import Mock
        client = Mock()
        client.spot_candles.side_effect = [[dict(bar, volume=100) for bar in self.bars(interval)]
                                         for interval in ('1h', '4h')]
        report = run_market_research(client, 'Spot', 'B-BTC_USDT', self.now)
        self.assertEqual(report['quality']['mode'], 'SHADOW ONLY')
        self.assertEqual(report['quality']['interval'], '1h')
        self.assertFalse(report['execution_enabled'])

    def test_provider_fallback_only_on_outage(self):
        from maxtrade.ai_review import ProviderUnavailable, routed_review
        from maxtrade.settings import AzureOpenAIConfig, ClaudeConfig
        config = AzureOpenAIConfig('https://example.com', 'model', 'v1', 'private',
                                  ClaudeConfig('claude-test', 'private'))
        for failure in (ValueError('refusal'), ValueError('invalid schema'),
                        ProviderUnavailable('HTTP 429')):
            with self.subTest(failure=str(failure)), patch('maxtrade.azure_ai.review_evidence', side_effect=failure), \
                    patch('maxtrade.ai_review.review_claude', return_value={'verdict': 'CLEAR'}) as fallback:
                if isinstance(failure, ProviderUnavailable):
                    self.assertTrue(routed_review(config, {}, True)['fallback'])
                    fallback.assert_called_once()
                else:
                    with self.assertRaises(ValueError):
                        routed_review(config, {}, True)
                    fallback.assert_not_called()
        with patch('maxtrade.azure_ai.review_evidence', return_value={'verdict': 'VETO'}), \
                patch('maxtrade.ai_review.review_claude') as fallback:
            self.assertEqual(routed_review(config, {}, True)['verdict'], 'VETO')
            fallback.assert_not_called()

    def bars(self, interval, count=80):
        duration = {"1h": 3600000, "4h": 14400000}[interval]
        end = int(self.now.timestamp() * 1000) // duration * duration
        return [{"time": end - (count - index) * duration, "open": 100,
                 "high": 101, "low": 99, "close": 100} for index in range(count)]

    def setUp(self):
        self.now = datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc)

    def test_evidence_excludes_forming_candle_and_records_provenance(self):
        bars = self.bars("1h")
        bars.append(dict(bars[-1], time=bars[-1]["time"] + 3600000, close=999))
        item = market_evidence(bars, "Spot", "B-BTC_USDT", "1h", self.now)
        self.assertEqual(item.values["close"], 100)
        self.assertEqual(item.price_unit, "USDT")
        self.assertIn("coindcx.com", item.source)
        self.assertEqual(item.event_time, "2026-10-06T06:00:00+00:00")
        self.assertEqual(item.expires_at, "2026-10-06T07:00:00+00:00")
        self.assertTrue(item.completed)
        self.assertEqual(item.event_time_kind, "candle_close")

    def test_missing_evidence_never_authorizes_trade(self):
        client = Mock()
        client.spot_candles.side_effect = [self.bars("1h"), ValueError("unavailable")]
        report = run_market_research(client, "Spot", "B-BTC_USDT", self.now)
        self.assertEqual(report["status"], "DATA ERROR")
        self.assertEqual(report["decision"], "NO TRADE")
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(len(report["errors"]), 1)
        self.assertTrue(report["blockers"])

    def test_stale_gapped_and_naive_evidence_rejected(self):
        bars = self.bars("1h")
        for invalid in (bars[:-1], bars[:20] + bars[21:]):
            with self.assertRaises(ValueError):
                market_evidence(invalid, "Spot", "B-BTC_USDT", "1h", self.now)
        with self.assertRaises(ValueError):
            market_evidence(bars, "Spot", "B-BTC_USDT", "1h", self.now.replace(tzinfo=None))

    def test_options_evidence_never_infers_premium_risk_levels(self):
        item = market_evidence(self.bars("4h"), "Options", "BTC", "4h", self.now)
        self.assertEqual(item.price_unit, "USD underlying")
        self.assertIsNone(item.values["entry"])
        self.assertIsNone(item.values["stop"])
        self.assertIsNone(item.values["target"])

    def test_alignment_and_options_bias_never_open_trade_gate(self):
        for product, actions, bias in (
                ("Futures", ("LONG", "LONG"), "LONG"),
                ("Futures", ("SHORT", "SHORT"), "SHORT"),
                ("Futures", ("LONG", "SHORT"), "NO TRADE"),
                ("Options", ("LONG", "LONG"), "CALL BIAS"),
                ("Options", ("SHORT", "SHORT"), "PUT BIAS")):
            with self.subTest(product=product, actions=actions):
                symbol = "BTC" if product == "Options" else "B-BTC_USDT"
                items = [replace(market_evidence(self.bars(interval), product, symbol, interval, self.now),
                                 action=action) for interval, action in zip(("1h", "4h"), actions)]
                with patch("maxtrade.research.market_evidence", side_effect=items):
                    report = run_market_research(Mock(), product, symbol, self.now)
                self.assertEqual(report["technical_bias"], bias)
                self.assertEqual(report["decision"], "NO TRADE")
                self.assertFalse(report["execution_enabled"])

    def test_report_survives_storage_reopen_without_polluting_scans(self):
        client = Mock()
        client.spot_candles.side_effect = [self.bars("1h"), self.bars("4h")]
        report = run_market_research(client, "Spot", "B-BTC_USDT", self.now)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "reports.sqlite3"
            saved_id = ScanHistory(path).save_research(report)
            restored = ScanHistory(path)
            self.assertEqual(restored.recent_research(), [{"id": saved_id, "report": report}])
            self.assertEqual(restored.recent(), [])

    def test_sentiment_validates_age_range_classification_and_provider_errors(self):
        payload = {"data": [{"value": "73", "value_classification": "Greed",
                             "timestamp": "1791244800"}], "metadata": {"error": None}}
        evidence = sentiment_evidence(payload, self.now)
        self.assertEqual(evidence["value"], 73)
        self.assertEqual(evidence["attribution"], "Alternative.me")
        self.assertEqual(evidence["expires_at"], "2026-10-07T00:00:00+00:00")
        for changed in ({"value": "101"}, {"timestamp": "0"}, {"timestamp": "1791331200"},
                        {"value_classification": "Unknown"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                sentiment_evidence({"data": [dict(payload["data"][0], **changed)]}, self.now)
        with self.assertRaises(ValueError):
            sentiment_evidence({"metadata": {"error": "unavailable"}}, self.now)
        session = Mock()
        session.get.return_value.json.return_value = payload
        self.assertEqual(fetch_sentiment(session, self.now)["value"], 73)
        session.get.assert_called_once_with("https://api.alternative.me/fng/", params={"limit": 1}, timeout=12)

    def test_news_is_bounded_deduplicated_and_never_event_clearance(self):
        item = '<item><title>Bitcoin market update</title><link>https://www.coindesk.com/markets/update</link><pubDate>Tue, 06 Oct 2026 06:00:00 GMT</pubDate></item>'
        feed = f'<rss><channel>{item}{item}</channel></rss>'.encode()
        evidence = news_evidence(feed, self.now)
        self.assertEqual(len(evidence["items"]), 1)
        self.assertFalse(evidence["event_clearance"])
        for invalid in (b'<!DOCTYPE rss><rss/>', b'<rss/>', feed.replace(b'2026', b'2020'),
                        feed.replace(b'https://www.coindesk.com', b'javascript:')):
            with self.assertRaises(ValueError):
                news_evidence(invalid, self.now)

    def test_derivatives_rejects_stale_crossed_and_nonfinite_data(self):
        item = {"instrument_name": "BTC-PERPETUAL", "state": "open", "timestamp": int(self.now.timestamp()*1000),
                "best_bid_price": 99999, "best_ask_price": 100001, "mark_price": 100000,
                "open_interest": 1000000, "funding_8h": 0.0001}
        evidence = derivatives_evidence({"result": item}, "BTC", self.now)
        self.assertTrue(evidence["liquid"])
        self.assertEqual(evidence["open_interest_unit"], "USD for inverse perpetual")
        for change in ({"timestamp": 0}, {"best_bid_price": 100002}, {"mark_price": float('nan')},
                       {"instrument_name": "ETH-PERPETUAL"}, {"open_interest": 0}, {"state": "closed"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                derivatives_evidence({"result": dict(item, **change)}, "BTC", self.now)

    def paper_report(self):
        client = Mock()
        client.spot_candles.side_effect = [self.bars('1h'), self.bars('4h')]
        report = run_market_research(client, 'Spot', 'B-BTC_USDT', self.now)
        report['technical_bias'] = 'LONG'
        for item in report['evidence']:
            item['action'] = 'LONG'
            item['values'].update(entry=100, stop=95, target=110)
        report['expires_at'] = '2026-10-06T08:00:00+00:00'
        for name in ('news', 'sentiment', 'derivatives'):
            report[name] = {'retrieved_at':self.now.isoformat(), 'expires_at':report['expires_at'], 'liquid':True}
        return report

    def test_paper_risk_is_fail_closed_and_requires_review(self):
        report = self.paper_report()
        account = {'capital':10000, 'equity':10000, 'daily_pnl':0, 'occupied':False, 'kill_switch':False}
        self.assertFalse(coordinate(report,self.now,False,account)['approved'])
        self.assertTrue(coordinate(report,self.now,True,account)['approved'])
        for changed in ({'kill_switch':True}, {'occupied':True}, {'daily_pnl':-300}):
            self.assertFalse(coordinate(report,self.now,True,dict(account,**changed))['approved'])
        report['news']['expires_at'] = self.now.isoformat()
        self.assertFalse(coordinate(report,self.now,True,account)['approved'])
        report['derivatives'] = None
        self.assertFalse(coordinate(report,self.now,True,account)['approved'])
        for invalid in ({'capital':10000}, dict(account,equity=float('nan'))):
            self.assertFalse(coordinate(self.paper_report(),self.now,True,invalid)['approved'])

    def test_historical_shadow_is_attached_but_cannot_change_risk(self):
        base = self.paper_report()
        account = {'capital':10000, 'equity':10000, 'daily_pnl':0, 'occupied':False, 'kill_switch':False}
        expected = coordinate(base, self.now, True, account)
        for shadow in ({'status':'AVAILABLE', 'regime':'DOWNTREND'}, {'status':'UNAVAILABLE'}):
            with patch('maxtrade.research.run_market_research', return_value=dict(base)), \
                    patch('maxtrade.historical_features.historical_context', return_value=shadow), \
                    patch('maxtrade.research.fetch_sentiment', return_value=base['sentiment']), \
                    patch('maxtrade.research.fetch_news', return_value=base['news']), \
                    patch('maxtrade.research.fetch_derivatives', return_value=base['derivatives']):
                report = run_coordinated_research(Mock(), 'Spot', 'B-BTC_USDT')
            self.assertEqual(report['historical_shadow'], shadow)
            self.assertEqual(coordinate(report, self.now, True, account), expected)
        with patch('maxtrade.research.run_market_research', return_value=dict(base)), \
                patch('maxtrade.historical_features.historical_context', side_effect=ValueError('checksum mismatch')), \
                patch('maxtrade.research.fetch_sentiment', return_value=base['sentiment']), \
                patch('maxtrade.research.fetch_news', return_value=base['news']), \
                patch('maxtrade.research.fetch_derivatives', return_value=base['derivatives']):
            report = run_coordinated_research(Mock(), 'Spot', 'B-BTC_USDT')
        self.assertEqual(report['historical_shadow']['status'], 'UNAVAILABLE')
        self.assertEqual(coordinate(report, self.now, True, account), expected)

    def test_autonomous_worker_submits_without_human_review_and_pause_cancels(self):
        from maxtrade.worker import run_once

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'autonomous.sqlite3'
            ledger = PaperLedger(path)
            with self.assertRaises(ValueError):
                ledger.submit(self.paper_report(), self.now, autonomous=True)
            ledger.set_automation(True)
            with patch('maxtrade.worker.CoinDCXClient'), patch('maxtrade.worker.PaperLedger.reconcile'), \
                    patch('maxtrade.worker.run_coordinated_research', return_value=self.paper_report()), \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.worker.datetime') as clock:
                clock.now.return_value = self.now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                self.assertEqual(run_once(path, ['B-BTC_USDT']), 0)
                self.assertEqual(run_once(path, ['B-BTC_USDT']), 0)
            self.assertEqual(len(ledger.positions()), 1)
            self.assertEqual(ledger.positions()[0]['state'], 'PENDING')
            self.assertEqual(ScanHistory(path).recent_research()[0]['report']['paper_policy'], 'autonomous-paper-v1')
            self.assertIsNone(ledger.performance()['win_rate_pct'])
            ledger.set_automation(False)
            self.assertEqual(ledger.positions()[0]['state'], 'CANCELLED')
            self.assertTrue(ledger.account(self.now)['kill_switch'])

    def test_worker_review_receives_current_paper_context_and_keeps_veto(self):
        import json
        from maxtrade.ai_review import encoded_evidence
        from maxtrade.worker import run_once

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'review-context.sqlite3'
            ledger = PaperLedger(path)
            ledger.set_automation(True)
            ledger.set_ai_mode('Azure-assisted')
            captured = {}

            def review(configuration, report):
                captured.update(json.loads(encoded_evidence(dict(report, private_key='not-for-review'))))
                return {'verdict': 'UNCERTAIN', 'summary': 'Liquidity coverage incomplete', 'concerns': ['spot depth missing']}

            with patch('maxtrade.worker.CoinDCXClient'), patch('maxtrade.worker.PaperLedger.reconcile'), \
                    patch('maxtrade.worker.run_coordinated_research', return_value=self.paper_report()), \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.ai_review.routed_review', side_effect=review), \
                    patch('maxtrade.worker.datetime') as clock:
                clock.now.return_value = self.now
                self.assertEqual(run_once(path, ['B-BTC_USDT'], ai_config=Mock()), 0)
            self.assertEqual(captured['paper_account']['as_of'], self.now.isoformat())
            self.assertEqual(captured['paper_account']['capital'], ledger.account(self.now)['capital'])
            self.assertEqual(captured['paper_account']['kill_switch'], ledger.account(self.now)['kill_switch'])
            self.assertNotIn('private_key', captured)
            performance = next(agent for agent in captured['agents'] if agent['agent'] == 'performance-analyst')
            self.assertEqual(performance['status'], 'AVAILABLE')
            reviewer = next(agent for agent in captured['agents'] if agent['agent'] == 'ai-reviewer')
            self.assertEqual(reviewer['status'], 'PENDING')
            self.assertEqual(reviewer['blockers'], [])
            self.assertFalse(reviewer['execution_enabled'])
            self.assertEqual(ledger.positions(), [])
            saved = ScanHistory(path).recent_research()[0]['report']
            self.assertIn('Azure review veto, uncertainty or unavailable', saved['blockers'])
            reviewer = next(agent for agent in saved['agents'] if agent['agent'] == 'ai-reviewer')
            self.assertEqual(reviewer['status'], 'AVAILABLE')

    def test_pending_review_does_not_hide_missing_or_failed_evidence(self):
        from maxtrade.research import manager_reports

        report = dict(self.paper_report(), ai_mode='Azure-assisted')
        for changes, pending, status, blockers in (
                ({}, False, 'UNAVAILABLE', ['Evidence unavailable']),
                ({}, True, 'PENDING', []),
                ({'ai_error': 'Azure OpenAI is not configured'}, True, 'UNAVAILABLE', ['Azure OpenAI is not configured']),
                ({'ai_review': {'verdict': 'VETO', 'concerns': ['event risk']}}, True, 'AVAILABLE', [])):
            with self.subTest(changes=changes, pending=pending):
                agents = manager_reports(dict(report, **changes), review_pending=pending)
                reviewer = next(agent for agent in agents if agent['agent'] == 'ai-reviewer')
                self.assertEqual(reviewer['status'], status)
                self.assertEqual(reviewer['blockers'], blockers)
                self.assertFalse(reviewer['execution_enabled'])
        account = {'capital': 10000, 'equity': 10000, 'daily_pnl': 0,
                   'occupied': False, 'kill_switch': False}
        report['agents'] = manager_reports(report, review_pending=True)
        self.assertFalse(coordinate(report, self.now, account=account, autonomous=True)['approved'])

    def test_autonomous_policy_retains_risk_vetoes(self):
        account = {'capital':10000, 'equity':10000, 'daily_pnl':0, 'occupied':False, 'kill_switch':False}
        report = self.paper_report()
        self.assertTrue(coordinate(report, self.now, account=account, autonomous=True)['approved'])
        for change in ({'kill_switch':True}, {'occupied':True}, {'daily_pnl':-300}):
            self.assertFalse(coordinate(report, self.now, account=dict(account, **change), autonomous=True)['approved'])
        report['news']['expires_at'] = self.now.isoformat()
        self.assertFalse(coordinate(report, self.now, account=account, autonomous=True)['approved'])

    def test_azure_review_is_structured_and_cannot_override_risk(self):
        import json
        from maxtrade.azure_ai import review_evidence
        from maxtrade.settings import AzureOpenAIConfig

        configuration = AzureOpenAIConfig('https://example.openai.azure.com', 'model', '2024-10-21', 'private-test')
        with patch('maxtrade.azure_ai.requests.Session') as session:
            response = session.return_value.__enter__.return_value.post.return_value
            response.status_code = 200
            response.content = b'{}'
            response.json.return_value = {'choices': [{'message': {'content': json.dumps(
                {'verdict': 'CLEAR', 'summary': 'Supplied evidence reviewed', 'concerns': []})}}]}
            review = review_evidence(configuration, self.paper_report())
            self.assertEqual(review['verdict'], 'CLEAR')
            self.assertNotIn('private-test', str(session.return_value.__enter__.return_value.post.call_args.kwargs['json']))
            response.json.return_value = {'choices': [{'message': {'content': json.dumps(
                {'verdict': ['CLEAR'], 'summary': 'Malformed verdict', 'concerns': []})}}]}
            with self.assertRaisesRegex(ValueError, 'invalid review schema'):
                review_evidence(configuration, self.paper_report())
            response.status_code = 401
            with self.assertRaises(ValueError):
                review_evidence(configuration, self.paper_report())
        account = {'capital':10000, 'equity':10000, 'daily_pnl':0, 'occupied':False, 'kill_switch':False}
        report = dict(self.paper_report(), ai_mode='Azure-assisted', ai_review=review)
        self.assertTrue(coordinate(report, self.now, account=account, autonomous=True)['approved'])
        self.assertFalse(coordinate(report, self.now, account=dict(account, kill_switch=True), autonomous=True)['approved'])
        for invalid in (None, {'verdict':'VETO'}, {'verdict':'UNCERTAIN'}, {'verdict':'CLEAR', 'concerns':['risk']}):
            report['ai_review'] = invalid
            self.assertFalse(coordinate(report, self.now, account=account, autonomous=True)['approved'])

    def test_azure_responses_request_and_fail_closed_output(self):
        import json
        from maxtrade.azure_ai import review_evidence
        from maxtrade.settings import azure_openai_config

        configuration = azure_openai_config({'AZURE_OPENAI_API_KEY': 'private-test'}, {})
        document = {'status': 'completed', 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(
                {'verdict': 'CLEAR', 'summary': 'Evidence reviewed', 'concerns': []})}]}]}
        with patch('maxtrade.azure_ai.requests.Session') as session:
            post = session.return_value.__enter__.return_value.post
            response = post.return_value
            response.status_code = 200
            response.content = b'{}'
            response.json.return_value = document
            self.assertEqual(review_evidence(configuration, self.paper_report())['verdict'], 'CLEAR')
            self.assertEqual(post.call_args.args[0], 'https://neilbisht.services.ai.azure.com/openai/v1/responses')
            self.assertEqual(post.call_args.kwargs['params'], {})
            body = post.call_args.kwargs['json']
            self.assertEqual(body['model'], 'gpt-6-astra-2')
            self.assertFalse(body['store'])
            self.assertEqual(body['text']['format']['type'], 'json_schema')
            self.assertNotIn('private-test', str(body))
            for invalid in (dict(document, status='incomplete'), dict(document, output=[]),
                            dict(document, content_filters=[{'blocked': True}]),
                            dict(document, output=[{'type': 'message', 'content': [{'type': 'refusal'}]}]),
                            dict(document, error={'code': 'server_error'}), {'status': 'completed', 'output': None}):
                response.json.return_value = invalid
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    review_evidence(configuration, self.paper_report())
            for http_code in (401, 403, 429, 500):
                response.status_code = http_code
                with self.subTest(http_code=http_code), self.assertRaises(ValueError):
                    review_evidence(configuration, self.paper_report())

    def test_paper_ledger_next_bar_costs_stop_first_reopen_and_duplicate(self):
        from datetime import timedelta
        with TemporaryDirectory() as folder:
            path = Path(folder)/'paper.sqlite3'
            ledger = PaperLedger(path)
            report = self.paper_report()
            with self.assertRaises(ValueError):
                ledger.submit(report,self.now,True)
            ledger.settings(10000,False)
            report['expires_at'] = '2026-10-06T06:32:00+00:00'
            ledger.submit(report,self.now,True)
            later = self.now + timedelta(hours=2)
            bars = self.bars('1h')
            bars += [dict(bars[-1],time=bars[-1]['time']+3600000),
                     dict(bars[-1],time=bars[-1]['time']+7200000,low=94,high=111)]
            ledger.reconcile('B-BTC_USDT',bars,later)
            position = PaperLedger(path).positions()[0]
            self.assertEqual(position['state'],'CLOSED')
            self.assertLess(position['pnl'],0)
            self.assertIn('STOP',position['reason'])
            self.assertGreater(position['entry'],100)
            self.assertGreaterEqual(position['pnl'],-100)
            performance = ledger.performance()
            self.assertEqual(performance['closed'], 1)
            self.assertEqual(performance['win_rate_pct'], 0)
            self.assertIsNone(performance['average_win'])
            self.assertAlmostEqual(performance['average_loss'], position['pnl'])
            self.assertAlmostEqual(performance['expectancy'], position['pnl'])
            self.assertAlmostEqual(performance['net_pnl'], position['pnl'])
            self.assertEqual(performance['daily'][0]['Closed'], 1)
            self.assertGreater(performance['max_drawdown_pct'], 0)
            with self.assertRaises(Exception):
                ledger.submit(report,self.now,True)
            with self.assertRaises(ValueError):
                ledger.settings(20000,False)

    def test_worker_deduplicates_slots_and_alerts_and_closes_client(self):
        from maxtrade.worker import run_once

        with TemporaryDirectory() as folder:
            path = Path(folder)/'worker.sqlite3'
            report = self.paper_report()
            with patch('maxtrade.worker.CoinDCXClient') as client, patch('maxtrade.worker.PaperLedger.reconcile'), \
                    patch('maxtrade.worker.run_coordinated_research',return_value=report) as research, \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.worker.datetime') as clock:
                clock.now.return_value = self.now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                self.assertEqual(run_once(path,['B-BTC_USDT']),0)
                self.assertEqual(run_once(path,['B-BTC_USDT']),0)
                research.assert_called_once()
                self.assertGreaterEqual(client.return_value.session.close.call_count, 1)
            history = ScanHistory(path)
            self.assertEqual(len(history.recent_research()),1)
            self.assertEqual(len(history.predictions()),1)
            self.assertEqual(history.worker_status()['mode'], 'one-shot')
            self.assertTrue((path.parent / 'backups' / 'paper-2026-10-06.sqlite3').exists())
            alert = next(row for row in history.recent_alerts() if row['symbol'] == 'B-BTC_USDT')
            self.assertEqual(alert['symbol'],'B-BTC_USDT')
            history.save_alert('same','now','BTC','first')
            history.save_alert('same','now','BTC','second')
            self.assertEqual(len(history.recent_alerts()),4)

    def test_paper_kill_and_opening_gap_cancel_pending_entries(self):
        from datetime import timedelta

        for kill in (True,False):
            with self.subTest(kill=kill), TemporaryDirectory() as folder:
                ledger = PaperLedger(Path(folder)/'paper.sqlite3')
                ledger.settings(10000,False)
                ledger.submit(self.paper_report(),self.now,True)
                if kill:
                    ledger.settings(10000,True)
                bars = self.bars('1h')
                bars += [dict(bars[-1],time=bars[-1]['time']+3600000),
                         dict(bars[-1],time=bars[-1]['time']+7200000,open=94,low=93,high=101)]
                ledger.reconcile('B-BTC_USDT',bars,self.now+timedelta(hours=2))
                position = ledger.positions()[0]
                self.assertEqual(position['state'],'CANCELLED')
                self.assertIsNone(position['entry'])
                self.assertFalse(ledger.account(self.now)['occupied'])

    def test_paper_missing_next_bar_cannot_open_later(self):
        from datetime import timedelta

        with TemporaryDirectory() as folder:
            ledger = PaperLedger(Path(folder)/'paper.sqlite3')
            ledger.settings(10000,False)
            ledger.submit(self.paper_report(),self.now,True)
            later = self.now + timedelta(hours=90)
            original = self.now
            self.now = later
            bars = self.bars('1h')
            self.now = original
            ledger.reconcile('B-BTC_USDT',bars,later)
            self.assertEqual(ledger.positions()[0]['state'],'CANCELLED')

    def test_worker_reconciliation_failure_preserves_research(self):
        from maxtrade.worker import run_once

        with TemporaryDirectory() as folder:
            path = Path(folder)/'worker.sqlite3'
            ledger = PaperLedger(path)
            ledger.set_automation(True)
            with patch('maxtrade.worker.CoinDCXClient') as client, \
                    patch('maxtrade.worker.PaperLedger.reconcile',side_effect=ValueError('missing bar')), \
                    patch('maxtrade.worker.run_coordinated_research',return_value=self.paper_report()), \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.worker.datetime') as clock, \
                    self.assertLogs(level='ERROR'):
                clock.now.return_value = self.now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                self.assertEqual(run_once(path,['B-BTC_USDT']),1)
                self.assertEqual(len(ScanHistory(path).recent_research()),1)
                self.assertEqual(ledger.positions(), [])
                self.assertIn('Paper reconciliation failed', ScanHistory(path).recent_research()[0]['report']['blockers'])
                self.assertGreaterEqual(client.return_value.session.close.call_count, 1)