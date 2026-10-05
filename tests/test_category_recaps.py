import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from flask import Flask

from category_recaps import CategoryRecaps, category, due_marker, recap_text, register_routes, ROME
from tarlo_daily import ArchivioOfferte

NOW = datetime(2026, 10, 6, 6, 5, tzinfo=ROME)


def product(i, title='Cuffie Bluetooth'):
    return dict(asin=f'B{i:09d}', titolo=title, prezzo_attuale='20,00', prezzo_precedente='40,00',
                tipo_riferimento='consigliato', sconto=50, disponibile=True, verificato_il=NOW.isoformat())


def message(i, text=None):
    return NS(id=i, raw_text=text or '🛒 Cuffie\n💰 20,00 €',
              entities=[NS(url=f'https://www.amazon.it/dp/B{i:09d}?tag=test')],
              date=NOW-timedelta(minutes=10))


class RecapTests(unittest.TestCase):
    def test_peak_hours_and_italian_date(self):
        for hour in (6,12,18,22):
            self.assertTrue(due_marker(NOW.replace(hour=hour)))
        for hour in (0,1,5,7,20,23):
            self.assertIsNone(due_marker(NOW.replace(hour=hour)))

    def test_categories_use_product_identity(self):
        for title, expected in [('Petkit lettiera per gatti', 'Animali'), ('Red Bull Energy Drink', 'Spesa'),
                                ('Mouse USB Logitech', 'Tecnologia'), ('Pril detersivo lavastoviglie', 'Casa'),
                                ('Mammut scarponi', 'Sport'), ('Asmodee gioco di carte', 'Giochi')]:
            self.assertIn(expected, category(product(1, title)))

    def test_html_links_references_and_limit(self):
        products = [product(i, '<Cuffie & modello> '+str(i)) for i in range(1,25)]
        text, chosen = recap_text(products, '#marker', NOW-timedelta(hours=6), NOW, 'tarlodelris06-21')
        self.assertEqual(len(chosen),3)  # At most three per category.
        self.assertLessEqual(len(text),3900)
        self.assertIn('&lt;Cuffie &amp;', text)
        self.assertIn('tag=tarlodelris06-21', text)
        self.assertIn('prezzo consigliato', text)

    def test_coupon_conditions_are_kept_without_inventing_source_coupon(self):
        p = product(1)
        p['coupon'] = dict(importo='10', unita='%', fonte='pagina_amazon', verificato_amazon=True)
        text,_ = recap_text([p], '#marker', NOW-timedelta(hours=6), NOW, 'test')
        self.assertIn('18,00 €', text)
        self.assertIn('attiva la casella', text)
        p['coupon']['fonte'] = 'canale_origine'
        text,_ = recap_text([p], '#marker', NOW-timedelta(hours=6), NOW, 'test')
        self.assertIn('20,00 €',text)
        self.assertNotIn('18,00 €',text)


class AsyncRecapTests(unittest.IsolatedAsyncioTestCase):
    def campaign(self, directory, messages, scraper, min_offers=12):
        async def history(*args,**kwargs):
            for m in messages:
                yield m
        bot = NS(send_message=AsyncMock(return_value=NS(message_id=900)))
        return CategoryRecaps(NS(iter_messages=history),bot,
                              ArchivioOfferte(Path(directory)/'db',database_url=''),scraper,
                              '@TarloDelRisparmio','tarlodelris06-21',min_offers)

    async def test_busy_window_sends_grouped_links_only_once(self):
        messages = [message(i) for i in range(1,13)]
        titles = ['Cuffie Bluetooth','Pril detersivo','Red Bull Energy Drink']
        scraper = Mock(side_effect=lambda asin,strict: product(int(asin[1:]), titles[int(asin[1:])%3]))
        with TemporaryDirectory() as directory:
            c = self.campaign(directory,messages,scraper)
            with patch('category_recaps.datetime',NS(now=lambda tz:NOW,fromisoformat=datetime.fromisoformat)):
                await c.tick(NOW)
                await c.tick(NOW)
            self.assertEqual(c.bot.send_message.await_count,1)
            text=c.bot.send_message.call_args.kwargs['text']
            self.assertIn('Casa e cucina',text)
            self.assertIn('Tecnologia',text)
            self.assertIn('Spesa e alimenti',text)
            self.assertEqual(c.state(due_marker(NOW))[0],'published')

    async def test_low_volume_and_recap_links_do_not_trigger_new_recap(self):
        messages=[message(1),message(2),message(3,'Recap #TarloRecapCategorie2026100518')]
        with TemporaryDirectory() as directory:
            scraper=Mock()
            c=self.campaign(directory,messages,scraper,min_offers=3)
            await c.tick(NOW)
            c.bot.send_message.assert_not_awaited()
            scraper.assert_not_called()
            self.assertEqual(c.state(due_marker(NOW))[0],'low_volume')

    async def test_no_verified_prices_means_no_recap(self):
        with TemporaryDirectory() as directory:
            c=self.campaign(directory,[message(i) for i in range(1,13)],Mock(return_value=None))
            with patch('category_recaps.datetime',NS(now=lambda tz:NOW,fromisoformat=datetime.fromisoformat)):
                await c.tick(NOW)
            c.bot.send_message.assert_not_awaited()

    async def test_stale_quotes_are_not_published(self):
        with TemporaryDirectory() as directory:
            old={**product(1),'verificato_il':(NOW-timedelta(minutes=6)).isoformat()}
            c=self.campaign(directory,[message(i) for i in range(1,13)],Mock(side_effect=lambda asin,strict:{**old,'asin':asin}))
            with patch('category_recaps.datetime',NS(now=lambda tz:NOW,fromisoformat=datetime.fromisoformat)):
                await c.tick(NOW)
            c.bot.send_message.assert_not_awaited()

    async def test_history_reconciles_after_lost_local_database(self):
        existing=message(5,due_marker(NOW))
        with TemporaryDirectory() as directory:
            c=self.campaign(directory,[existing],Mock())
            await c.tick(NOW)
            c.bot.send_message.assert_not_awaited()
            self.assertEqual(c.state(due_marker(NOW))[1]['message_id'],5)

    async def test_ambiguous_send_is_not_retried_after_restart(self):
        with TemporaryDirectory() as directory:
            c=self.campaign(directory,[],None)
            c.bot.send_message.side_effect=TimeoutError()
            await c.send_once('#marker','Test',[],{})
            other=self.campaign(directory,[],None)
            await other.send_once('#marker','Test',[],{})
            self.assertEqual(c.bot.send_message.await_count,1)
            other.bot.send_message.assert_not_awaited()
            self.assertEqual(other.state('#marker')[0],'attempted')

    async def test_wrong_destination_and_unreadable_history_block(self):
        with TemporaryDirectory() as directory:
            c=self.campaign(directory,[],None)
            c.channel='@other'
            with self.assertRaises(RuntimeError): await c.tick(NOW)
            c.channel='@TarloDelRisparmio'
            c.history=AsyncMock(side_effect=RuntimeError('unavailable'))
            with self.assertRaises(RuntimeError): await c.tick(NOW)
            c.bot.send_message.assert_not_awaited()

    async def test_status_endpoint_is_read_only_and_has_no_secrets(self):
        with TemporaryDirectory() as directory:
            c=self.campaign(directory,[],None)
            app=Flask('recap-test'); register_routes(app,c)
            response=app.test_client().get('/recaps/status')
            self.assertEqual(response.json['hours'],[6,12,18,22])
            self.assertNotIn('tag',response.json)
            self.assertEqual(app.test_client().post('/recaps/status').status_code,405)
            c.bot.send_message.assert_not_awaited()


if __name__=='__main__': unittest.main()
