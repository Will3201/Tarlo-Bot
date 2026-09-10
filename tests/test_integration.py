"""Controlli offline: nessuna connessione a Telegram o Amazon."""
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Credenziali sintetiche valide solo per costruire gli oggetti, mai usate in rete.
with patch.dict(os.environ, {
    'TELEGRAM_TOKEN': '123456789:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN',
    'TELEGRAM_API_ID': '1', 'TELEGRAM_API_HASH': 'test-only',
    'TELEGRAM_SESSION_STRING': 'test-only', 'DATABASE_URL': '',
}), patch('telegram.request.HTTPXRequest'), patch('telegram.Bot'):
    import main
from PIL import Image
from flask import Flask
from tarlo_daily import ArchivioOfferte
from daily_pipeline import DailyPipeline, register_routes

HTML = '''<html><input id="ASIN" value="B012345678">
<span id="productTitle">Prodotto dimostrativo</span>
<div id="corePriceDisplay_desktop_feature_div">
 <span class="a-price" data-a-size="xl"><span class="a-price-whole">20,</span><span class="a-price-fraction">00</span></span>
 <span class="basisPrice">Prezzo consigliato: <span class="a-offscreen">40,00 €</span></span>
</div>
<span id="acrPopover" title="4,5 su 5 stelle"></span>
<span id="acrCustomerReviewText">1.234 valutazioni</span>
<div id="socialProofingAsinFaceout_feature_div">100+ acquistati nel mese scorso</div>
<div id="availability">Disponibile</div><input id="add-to-cart-button">
<img id="landingImage" src="https://example.invalid/test.png"></html>'''


class IntegrationTests(unittest.TestCase):
    def scrape(self, html):
        response = Mock(status_code=200, text=html)
        with patch.object(main.requests, 'get', return_value=response), patch.object(main, 'print'):
            return main.scarica_dettagli_amazon('B012345678', strict=True)

    def test_price_reference_and_metrics(self):
        p = self.scrape(HTML)
        self.assertEqual(p['prezzo_attuale'], '20,00')
        self.assertEqual(p['prezzo_precedente'], '40,00')
        self.assertEqual(p['tipo_riferimento'], 'consigliato')
        self.assertEqual(p['numero_recensioni'], 1234)
        self.assertEqual(p['acquisti_mese_min'], 100)
        self.assertEqual(p['stelle'], 4.5)

    def test_wrong_variant_skipped(self):
        self.assertIsNone(self.scrape(HTML.replace('B012345678', 'B012345679')))

    def test_related_price_not_used(self):
        self.assertIsNone(self.scrape(HTML.replace('corePriceDisplay_desktop_feature_div', 'related-products')))

    def test_unavailable_detected(self):
        self.assertFalse(self.scrape(HTML.replace('>Disponibile<', '>Attualmente non disponibile<'))['disponibile'])

    def test_actual_render_and_http_feed(self):
        p = self.scrape(HTML)
        raw = BytesIO()
        Image.new('RGB', (120, 160), '#dddddd').save(raw, 'PNG')
        with patch.object(main.requests, 'get', return_value=Mock(content=raw.getvalue())):
            png = main.crea_immagine(p, require_image=True)
        im = Image.open(BytesIO(png))
        self.assertEqual(im.size, (1080, 1080))
        self.assertEqual(im.format, 'PNG')
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchivioOfferte(Path(directory) / 'db', database_url='')
            archive.registra(p, 1)
            pipeline = DailyPipeline(archive, lambda asin, strict: dict(p), lambda product: png)
            self.assertEqual(pipeline.prepare(), 'ready')
            app = Flask('integration-test')
            register_routes(app, pipeline)
            client = app.test_client()
            response = client.get('/daily/latest')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['status'], 'ready')
            self.assertEqual(client.get(data['media_path']).data, png)
            self.assertEqual(client.get('/daily/media/2026-01-01/wrong.png').status_code, 404)
            self.assertNotIn('TELEGRAM_TOKEN', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
