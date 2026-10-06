import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import Mock, patch

from maxtrade.research import fetch_sentiment, market_evidence, run_market_research, sentiment_evidence
from maxtrade.history import ScanHistory
from maxtrade.research_sources import derivatives_evidence, news_evidence
from maxtrade.paper import PaperLedger, coordinate


class ResearchTests(unittest.TestCase):
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
                    patch('maxtrade.worker.datetime') as clock:
                clock.now.return_value = self.now
                self.assertEqual(run_once(path,['B-BTC_USDT']),0)
                self.assertEqual(run_once(path,['B-BTC_USDT']),0)
                research.assert_called_once()
                client.return_value.session.close.assert_called_once()
            history = ScanHistory(path)
            self.assertEqual(len(history.recent_research()),1)
            alert = history.recent_alerts()[0]
            self.assertEqual(alert['symbol'],'B-BTC_USDT')
            history.save_alert('same','now','BTC','first')
            history.save_alert('same','now','BTC','second')
            self.assertEqual(len(history.recent_alerts()),2)

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
            with patch('maxtrade.worker.CoinDCXClient') as client, \
                    patch('maxtrade.worker.PaperLedger.reconcile',side_effect=ValueError('missing bar')), \
                    patch('maxtrade.worker.run_coordinated_research',return_value=self.paper_report()), \
                    self.assertLogs(level='ERROR'):
                self.assertEqual(run_once(path,['B-BTC_USDT']),1)
                self.assertEqual(len(ScanHistory(path).recent_research()),1)
                client.return_value.session.close.assert_called_once()