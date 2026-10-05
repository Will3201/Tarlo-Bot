import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from amazon_product import extract_product, normalizza_titolo
from text_layout import disegna_testi
from test_integration import HTML, main

ASIN = 'B012345678'


class ProductIntegrityTests(unittest.TestCase):
    def test_model_quantity_and_decimal_comma_are_preserved(self):
        title = '  Marca, Modello X-2500, 1,5 L, confezione da 24 pezzi  '
        self.assertEqual(normalizza_titolo(title), title.strip())
        p = extract_product(HTML.replace('Prodotto dimostrativo', title), ASIN)
        self.assertEqual(p['titolo'], title.strip())

    def test_identity_and_title_required_even_without_strict_flag(self):
        for page in [HTML.replace(ASIN, 'B012345679'),
                     HTML.replace('id="productTitle"', 'id="relatedTitle"'),
                     HTML.replace('id="ASIN"', 'id="unknown"')]:
            with patch.object(main.requests, 'get', return_value=Mock(status_code=200, text=page)):
                self.assertIsNone(main.scarica_dettagli_amazon(ASIN))

    def test_empty_primary_block_does_not_hide_real_apex_price(self):
        page = HTML.replace('corePriceDisplay_desktop_feature_div', 'apex_desktop')
        page += '<div id="corePriceDisplay_desktop_feature_div"></div>'
        self.assertEqual(extract_product(page, ASIN)['prezzo_attuale'], '20,00')

    def test_per_unit_and_installment_prices_are_not_product_price(self):
        extra = ('<span id="unitPrice"><span class="a-price" data-a-size="xl">'
                 '<span class="a-offscreen">0,25 €</span></span>/100 ml</span>'
                 '<div id="installmentPrice"><span class="a-price" data-a-size="xl">'
                 '<span class="a-offscreen">1,99 €</span></span></div>')
        page = HTML.replace('<span class="a-price" data-a-size="xl">',
                            extra + '<span class="a-price" data-a-size="xl">', 1)
        self.assertEqual(extract_product(page, ASIN)['prezzo_attuale'], '20,00')

    def test_hidden_and_related_prices_are_never_fallbacks(self):
        hidden = HTML.replace('data-a-size="xl"', 'data-a-size="xl" style="display:none"')
        self.assertIsNone(extract_product(hidden, ASIN))
        self.assertIsNone(extract_product(HTML.replace('corePriceDisplay_desktop_feature_div', 'related'), ASIN))

    def test_ambiguous_current_prices_block_publication(self):
        extra = '<span class="a-price" data-a-size="xl"><span class="a-offscreen">1,00 €</span></span>'
        self.assertIsNone(extract_product(HTML.replace('</div>', extra + '</div>', 1), ASIN))

    def test_reference_formats_and_labels_stay_associated(self):
        page = HTML.replace('40,00 €', '€ 40,00').replace('Prezzo consigliato', 'Prezzo mediano')
        p = extract_product(page, ASIN)
        self.assertEqual((p['prezzo_precedente'], p['tipo_riferimento'], p['sconto']), ('40,00', 'mediano', 50))
        split = HTML.replace('<span class="a-offscreen">40,00 €</span>',
                             '<span class="a-price-whole">40,</span><span class="a-price-fraction">00</span>')
        self.assertEqual(extract_product(split, ASIN)['prezzo_precedente'], '40,00')

    def test_unavailable_and_bad_identity_are_not_published(self):
        p = extract_product(HTML.replace('>Disponibile<', '>Non disponibile<'), ASIN)
        self.assertFalse(p['disponibile'])
        self.assertIsNone(extract_product('automated access', ASIN))

    def render(self, **fields):
        p = dict(titolo='Prodotto dimostrativo, modello X, confezione 24 pezzi',
                 prezzo_attuale='20,00', prezzo_precedente='40,00', tipo_riferimento='consigliato', sconto=99)
        p.update(fields)
        im = Image.new('RGB', (1080, 1080), 'black')
        return disegna_testi(ImageDraw.Draw(im), p)

    def test_discount_is_recomputed_from_same_prices_not_stale_field(self):
        r = self.render()
        self.assertEqual(r['sconto']['text'], '-50%')
        self.assertEqual(r['riferimento']['text'], 'PREZZO CONSIGLIATO')
        self.assertIn('24 pezzi', r['titolo']['text'].replace('\n', ' '))

    def test_missing_reference_never_leaves_empty_boxes(self):
        r = self.render(prezzo_precedente=None)
        self.assertIn('NON DISPONIBILE', r['riferimento']['text'].replace('\n', ' '))
        self.assertIn('N/D', r['sconto']['text'])
        self.assertEqual(r['prezzo']['text'], '20,00 €')

    def test_invalid_current_price_blocks_rendering(self):
        for price in [None, '0,00', 'nan', 'unavailable', '0,25 €/100 ml']:
            with self.assertRaises(ValueError):
                self.render(prezzo_attuale=price)

    def test_large_prices_and_long_titles_fit_their_boxes(self):
        r = self.render(titolo='Marca Modello ' + 'caratteristica lunga ' * 30,
                        prezzo_attuale='123.456,78', prezzo_precedente='999.999,99')
        for key, box in [('titolo', (570,224,1034,378)), ('prezzo', (580,478,1024,672)),
                         ('precedente',(580,750,1024,807)), ('sconto',(703,858,985,946))]:
            left, top, right, bottom = r[key]['bounds']
            self.assertTrue(box[0] <= left <= right <= box[2] and box[1] <= top <= bottom <= box[3])

    def test_missing_or_broken_product_photo_blocks_required_image(self):
        p = dict(titolo='Test', prezzo_attuale='1,99', immagine_url='')
        with self.assertRaises(ValueError):
            main.crea_immagine(p, require_image=True)
        p['immagine_url'] = 'https://example.invalid/test'
        with patch.object(main.requests, 'get', return_value=Mock(content=b'not an image')):
            with self.assertRaises(Exception):
                main.crea_immagine(p, require_image=True)


if __name__ == '__main__':
    unittest.main()
