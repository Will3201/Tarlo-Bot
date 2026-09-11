import ast
import asyncio
from pathlib import Path
import unittest
from unittest.mock import Mock, patch, AsyncMock
from PIL import Image
from bs4 import BeautifulSoup
from pesca_rules import e_pesca, coupon_amazon
from pesca_layout import componi
import pesca_amazon

HTML='''<input id="ASIN" value="B012345678"><span id="productTitle">Shimano mulinello da pesca, modello 2500</span><div id="corePriceDisplay_desktop_feature_div"><span class="a-price" data-a-size="xl"><span class="a-offscreen">20,00 €</span></span><span class="basisPrice">Prezzo consigliato: <span class="a-offscreen">40,00 €</span></span></div><div id="availability">Disponibile</div><input id="add-to-cart-button"><img id="landingImage" src="https://example.invalid/photo.jpg"><div id="couponFeature"><input type="checkbox">Risparmia 10% con coupon</div>'''

class PescaTests(unittest.TestCase):
    def scrape(self, html):
        with patch.object(pesca_amazon.requests,'get',return_value=Mock(status_code=200,text=html)):
            return pesca_amazon.scarica_dettagli_amazon('B012345678',strict=True)
    def test_relevance(self):
        for title in ['Shimano mulinello pesca 2500','Canna da pesca spinning','Jerkbait 10 cm','Occhiali polarizzati pesca']:
            self.assertTrue(e_pesca(title), title)
        for title in ['Shimano cambio bicicletta','Cucchiaino da cucina','Sleeping bag campeggio','Tè alla pesca','Canna fumaria acciaio','Piombo per saldatura']:
            self.assertFalse(e_pesca(title), title)
    def test_scrape_price_coupon(self):
        p=self.scrape(HTML)
        self.assertEqual(p['prezzo_attuale'],'20,00')
        self.assertEqual(p['sconto'],50)
        self.assertEqual(p['coupon']['importo'],'10')
        self.assertTrue(p['coupon']['casella'])
        self.assertIn('modello 2500',p['titolo'])
    def test_wrong_variant_and_related(self):
        self.assertIsNone(self.scrape(HTML.replace('B012345678','B098765432')))
        self.assertIsNone(self.scrape(HTML.replace('corePriceDisplay_desktop_feature_div','related')))
        self.assertFalse(self.scrape(HTML.replace('>Disponibile<','>Non disponibile<'))['disponibile'])
    def test_coupon_not_from_related(self):
        self.assertIsNone(coupon_amazon(BeautifulSoup('<div>Coupon 20%</div>','html.parser')))
    def test_layout(self):
        base=Image.new('RGBA',(1080,1080),'white')
        result=componi(base,Image.new('RGB',(100,300),'blue'),{'titolo':'Mulinello da pesca Shimano 2500','prezzo_attuale':'1.234,56','prezzo_precedente':'2.999,99','sconto':59})
        boxes=[(585,225,985,395),(535,485,1030,725),(550,810,970,887),(600,910,930,994)]
        for obj,box in zip(result,boxes):
            x,y,r,b=obj['bounds']; l,t,right,bottom=box
            self.assertTrue(l<=x<r<=right and t<=y<b<=bottom)
            self.assertAlmostEqual(x+r,l+right)
            self.assertAlmostEqual(y+b,t+bottom)
    def test_send_marks_after_success_only(self):
        # Esegue la funzione reale senza inizializzare credenziali o connessioni.
        source=Path('bot_pesca.py').read_text(); module=ast.parse(source)
        node=next(n for n in module.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='processa_asin')
        class NetworkError(Exception): pass
        product={'asin':'B012345678','titolo':'Mulinello pesca','prezzo_attuale':'20,00','prezzo_precedente':'40,00','sconto':50,'tipo_riferimento':'consigliato'}
        for error,expected in [(None,True),(ValueError(),False),(NetworkError(),True)]:
            sent=Mock(); title=Mock()
            env={'asyncio':asyncio,'gia_inviato':lambda a:False,'scarica_dettagli_amazon':lambda a:product,'offerta_valida':lambda p:True,'normalizza_titolo':lambda t:t,'titolo_gia_inviato':lambda t:False,'crea_immagine':lambda p:b'png','AMAZON_TAG':'test','escape_markdown':lambda x,version:x,'frase_iniziale':lambda x:'','avviso_coupon':lambda x:'','bot':Mock(send_photo=AsyncMock(side_effect=error)),'CANALE_CHAT_ID':'test','BytesIO':__import__('io').BytesIO,'NetworkError':NetworkError,'segna_inviato':sent,'segna_titolo_inviato':title}
            exec(compile(ast.Module(body=[node],type_ignores=[]),'bot_pesca.py','exec'),env)
            result=asyncio.run(env['processa_asin']('B012345678'))
            self.assertEqual(sent.called,expected)
            self.assertEqual(result,error is None)

if __name__=='__main__': unittest.main()
