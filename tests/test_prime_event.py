import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

from prime_event import (PrimeCampaign, ROME, START, END, WELCOME, due_marker,
                         event_active, checked_product, recap_text, message_asins)
from tarlo_daily import ArchivioOfferte


class PrimeTests(unittest.TestCase):
    def test_time_bounds_and_no_catchup(self):
        self.assertFalse(event_active(START - timedelta(seconds=1)))
        self.assertTrue(event_active(START))
        self.assertFalse(event_active(END))
        self.assertIsNone(due_marker(datetime(2026, 10, 6, 13, tzinfo=ROME)))
        self.assertEqual(due_marker(datetime(2026, 10, 7, 20, 59, tzinfo=ROME)), '#TarloPrime2026100720')

    def test_only_explicit_asins_and_available_products(self):
        m = NS(raw_text='https://www.amazon.it/dp/B012345678?tag=test', entities=[])
        self.assertEqual(message_asins(m), ['B012345678'])
        p = dict(asin='B012345678', titolo='Test', prezzo_attuale='20,00', disponibile=True)
        self.assertTrue(checked_product(p, p['asin']))
        self.assertFalse(checked_product({**p, 'disponibile': False}, p['asin']))
        self.assertFalse(checked_product(p, 'B999999999'))

    def test_caption_does_not_invent_reference_or_coupon(self):
        p = dict(asin='B012345678', titolo='<Prodotto>', prezzo_attuale='20,00', prezzo_precedente='80,00',
                 tipo_riferimento='non_identificato', sconto=75)
        text = recap_text([p], '#marker', START, 'tarlodelris06-21')
        self.assertIn('&lt;Prodotto&gt;', text)
        self.assertIn('tag=tarlodelris06-21', text)
        self.assertNotIn('−75%', text)
        self.assertNotIn('errore di prezzo', text)


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_recap_checks_price_twice_and_sends_only_once(self):
        now = datetime(2026, 10, 6, 12, 5, tzinfo=ROME)
        welcome = NS(raw_text=WELCOME, id=10, date=now, entities=[])
        recent = NS(raw_text='https://www.amazon.it/dp/B012345678?tag=old', id=11,
                    date=now-timedelta(minutes=10), entities=[])
        old = NS(raw_text='https://www.amazon.it/dp/B999999999', id=12,
                 date=now-timedelta(hours=7), entities=[])
        product = dict(asin='B012345678', titolo='Test', prezzo_attuale='20,00', disponibile=True)
        async def messages(*args, **kwargs):
            for message in [welcome, recent, old]:
                yield message
        with TemporaryDirectory() as directory:
            scraper = Mock(return_value=product)
            bot = NS(send_message=AsyncMock(return_value=NS(message_id=900)))
            campaign = PrimeCampaign(NS(iter_messages=messages), bot,
                ArchivioOfferte(Path(directory)/'state.db', database_url=''), scraper,
                '@TarloDelRisparmio', 'tarlodelris06-21')
            with patch('prime_event.datetime') as clock:
                clock.now.return_value = now
                await campaign.tick(now)
                await campaign.tick(now)
            self.assertEqual(scraper.call_count, 2)
            self.assertEqual(bot.send_message.await_count, 1)
            self.assertEqual(campaign.state('#TarloPrime2026100612')[0], 'published')

    async def test_unreadable_channel_never_publishes(self):
        with TemporaryDirectory() as directory:
            bot = NS(send_message=AsyncMock())
            campaign = PrimeCampaign(None, bot, ArchivioOfferte(Path(directory)/'state.db', database_url=''),
                                     None, '@TarloDelRisparmio', 'test-21')
            campaign.history = AsyncMock(side_effect=RuntimeError('access denied'))
            with self.assertRaises(RuntimeError):
                await campaign.tick(START)
            bot.send_message.assert_not_awaited()

    async def test_ambiguous_send_survives_new_campaign_instance(self):
        with TemporaryDirectory() as directory:
            archive = ArchivioOfferte(Path(directory)/'state.db', database_url='')
            bot = NS(send_message=AsyncMock(side_effect=TimeoutError()))
            campaign = PrimeCampaign(None, bot, archive, None, '@TarloDelRisparmio', 'test-21')
            await campaign.send_once('#marker', 'Test', [])
            other = PrimeCampaign(None, bot, archive, None, '@TarloDelRisparmio', 'test-21')
            await other.send_once('#marker', 'Test', [])
            self.assertEqual(bot.send_message.await_count, 1)
            self.assertEqual(other.state('#marker')[0], 'attempted')

    async def test_history_reconciles_without_resend(self):
        with TemporaryDirectory() as directory:
            archive = ArchivioOfferte(Path(directory)/'state.db', database_url='')
            bot = NS(send_message=AsyncMock())
            campaign = PrimeCampaign(None, bot, archive, None, '@TarloDelRisparmio', 'test-21')
            campaign.record('#marker', 'attempted', {})
            await campaign.send_once('#marker', 'Test', [NS(raw_text='Test\n#marker', id=123)])
            bot.send_message.assert_not_awaited()
            self.assertEqual(campaign.state('#marker')[1]['message_id'], 123)

    async def test_wrong_destination_blocked(self):
        with TemporaryDirectory() as directory:
            campaign = PrimeCampaign(None, None, ArchivioOfferte(Path(directory)/'state.db', database_url=''),
                                     None, '@anotherchannel', 'test-21')
            with self.assertRaises(RuntimeError):
                await campaign.tick(START)


if __name__ == '__main__':
    unittest.main()
