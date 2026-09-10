import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tarlo_daily import ArchivioOfferte, ROME, valuta
from daily_pipeline import DailyPipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = ArchivioOfferte(Path(self.temp.name) / 'db', database_url='')
        self.p = dict(asin='B012345678', titolo='Prodotto dimostrativo',
                      prezzo_attuale='20,00', prezzo_precedente='40,00',
                      sconto=50, tipo_riferimento='consigliato', disponibile=True,
                      immagine_url='https://example.invalid/product.png')
        self.archive.registra(self.p, 10)
        self.pipeline = DailyPipeline(self.archive, lambda asin, strict: dict(self.p),
                                      lambda p: b'fake-image-test-only')

    def test_one_daily_draft(self):
        self.assertEqual(self.pipeline.prepare(), 'ready')
        first = self.pipeline.latest()
        self.assertEqual(self.pipeline.prepare(), 'already_claimed_or_ready')
        self.assertEqual(first, self.pipeline.latest())
        self.assertIn('prezzo consigliato', first['caption'])
        self.assertNotIn('più venduto', first['caption'])
        self.assertEqual(first['status'], 'ready')

    def test_claim_excludes_second_worker(self):
        now = datetime.now(timezone.utc)
        day = now.astimezone(ROME).date().isoformat()
        self.assertTrue(self.pipeline.claim(day, now))
        self.assertIsNone(self.pipeline.claim(day, now))

    def test_expired_lease_can_recover(self):
        now = datetime.now(timezone.utc)
        day = now.astimezone(ROME).date().isoformat()
        old = self.pipeline.claim(day, now - timedelta(minutes=16))
        new = self.pipeline.claim(day, now)
        self.assertTrue(new)
        self.assertNotEqual(old, new)

    def test_no_available_product_releases_claim(self):
        self.p['disponibile'] = False
        self.assertEqual(self.pipeline.prepare(), 'no_verified_offer')
        self.assertEqual(self.pipeline.latest()['status'], 'not_ready')
        self.p['disponibile'] = True
        self.assertEqual(self.pipeline.prepare(), 'ready')

    def test_unknown_reference_not_published(self):
        self.p['tipo_riferimento'] = 'non_identificato'
        self.assertEqual(self.pipeline.prepare(), 'no_verified_offer')

    def test_changed_price_skipped(self):
        calls = []
        def scrape(asin, strict):
            calls.append(asin)
            return dict(self.p, prezzo_attuale='20,00' if len(calls) == 1 else '30,00')
        self.pipeline.scrape = scrape
        self.assertEqual(self.pipeline.prepare(), 'no_verified_offer')

    def test_image_failure_skips(self):
        def bad(p):
            raise ValueError('test')
        self.pipeline.render = bad
        with self.assertLogs('daily_pipeline', level='ERROR'):
            self.assertEqual(self.pipeline.prepare(), 'no_verified_offer')
        self.assertEqual(self.pipeline.latest()['status'], 'not_ready')

    def test_expired_post_not_served(self):
        self.pipeline.prepare()
        data = self.pipeline.latest()
        later = datetime.fromisoformat(data['valid_until']) + timedelta(seconds=1)
        self.assertIn(self.pipeline.latest(later)['status'], ('expired', 'not_ready'))

    def test_wrong_media_id_not_served(self):
        self.pipeline.prepare()
        data = self.pipeline.latest()
        ident = data['media_path'].split('/')[-1][:-4]
        self.assertIsNone(self.pipeline.media(data['day'], 'incorrect'))
        self.assertEqual(self.pipeline.media(data['day'], ident), b'fake-image-test-only')

    def test_missing_reviews_not_invented(self):
        score = valuta(self.p)
        self.assertIn('numero_recensioni', score['dati_mancanti'])
        self.assertNotIn('recensioni', score['componenti'])

    def test_rome_day_and_duplicate(self):
        now = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
        for mid, hour in [(1, 21), (2, 22)]:
            self.archive.registra(dict(self.p, asin=f'B01234567{mid}'), mid,
                                  when=datetime(2026, 9, 9, hour, tzinfo=timezone.utc))
        rows = [x for x in self.archive.candidati(now) if x['prodotto']['asin'] != self.p['asin']]
        self.assertEqual([x['prodotto']['asin'] for x in rows], ['B012345672'])


if __name__ == '__main__':
    unittest.main()
