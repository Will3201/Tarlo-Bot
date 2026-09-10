import asyncio
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from free_daily import product_from_message, recover_today, register_free_routes
from tarlo_daily import ArchivioOfferte


class FreeTests(unittest.TestCase):
    def message(self, ident=1, date=None, url='https://www.amazon.it/dp/B012345678?tag=test'):
        return SimpleNamespace(id=ident, date=date or datetime.now(timezone.utc),
            raw_text='🛒 Prodotto di prova\n💰 20,00 €\nRiferimento Amazon: 40,00 € (-50%).',
            entities=[SimpleNamespace(url=url)])

    def test_hidden_link_and_missing_metrics(self):
        product = product_from_message(self.message())
        self.assertEqual(product['asin'], 'B012345678')
        self.assertEqual(product['prezzo_precedente'], '40,00')
        self.assertNotIn('stelle', product)

    def test_ambiguous_product_skipped(self):
        msg = self.message()
        msg.entities.append(SimpleNamespace(url='https://amazon.it/dp/B012345679'))
        self.assertIsNone(product_from_message(msg))

    def test_history_uses_rome_day_and_keeps_richer_record(self):
        now = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
        messages = [self.message(1, now-timedelta(hours=1)),
                    self.message(2, datetime(2026,9,9,22,30,tzinfo=timezone.utc)),
                    self.message(3, datetime(2026,9,9,21,30,tzinfo=timezone.utc))]
        class Client:
            async def iter_messages(self, channel, limit):
                for msg in messages:
                    yield msg
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchivioOfferte(Path(directory)/'db', database_url='')
            p = dict(product_from_message(messages[0]), stelle=4.8)
            archive.registra(p, 1, when=messages[0].date)
            count = asyncio.run(recover_today(Client(), archive, 'TarloDelRisparmio', now))
            self.assertEqual(count, 2)
            self.assertEqual(archive.candidati(now)[0]['prodotto']['stelle'], 4.8)

    def test_post_rejects_external_parameters(self):
        coordinator = Mock()
        coordinator.request.return_value = ({'status':'preparing'}, 202)
        app = Flask('free-test')
        register_free_routes(app, coordinator)
        client = app.test_client()
        self.assertEqual(client.get('/daily/prepare').status_code,405)
        self.assertEqual(client.post('/daily/prepare', json={'url':'http://example.com'}).status_code,400)
        self.assertEqual(client.post('/daily/prepare?url=test').status_code,400)
        self.assertEqual(client.post('/daily/prepare', headers={'Origin':'https://example.com'}).status_code,400)
        self.assertEqual(client.post('/daily/prepare').status_code,202)
        coordinator.request.assert_called_once()
