"""Preparazione su richiesta usando il canale pubblico come archivio gratuito.

Non pubblica messaggi. Il POST accetta solo un comando fisso, senza URL o prodotti
forniti dal chiamante, e limita le preparazioni a una ogni 15 minuti per processo.
"""
import asyncio
import base64
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from io import BytesIO

from PIL import Image

from tarlo_daily import ROME, number
from daily_pipeline import publication_slot

LOG = logging.getLogger(__name__)


def product_from_message(message):
    text = message.raw_text or ''
    urls = [getattr(entity, 'url', '') or '' for entity in (message.entities or [])]
    asins = set(re.findall(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#\s]|$)',
                           '\n'.join([text, *urls])))
    price = re.search(r'💰\s*\**\s*([\d.,]+)\s*€', text)
    if len(asins) != 1 or not price or not number(price.group(1)):
        return None
    reference = re.search(r'(?:anziché|Riferimento Amazon:)\s*([\d.,]+)\s*€', text, re.I)
    title = re.search(r'🛒\s*([^\n]+)', text)
    return {'asin': asins.pop(), 'titolo': title.group(1).strip('* ') if title else 'Prodotto Amazon',
            'prezzo_attuale': price.group(1),
            'prezzo_precedente': reference.group(1) if reference else None,
            'fonte_recupero': 'post_del_canale_da_ricontrollare'}


async def recover_today(client, archive, channel, now=None):
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=6)
    count = 0
    async for message in client.iter_messages(channel, limit=None):
        if message.date < start:
            break
        if message.date > now:
            continue
        product = product_from_message(message)
        if product:
            await asyncio.to_thread(archive.registra, product, message.id, channel, message.date)
            count += 1
    return count


async def prepare_reported_offer(pipeline, client, channel, now=None):
    """Fallback dichiarato: prezzo storico nel post, mai spacciato per prezzo live."""
    now = now or datetime.now(timezone.utc)
    day = publication_slot(now)
    owner = pipeline.claim(day, now)
    if owner is None:
        return 'already_claimed_or_ready'
    try:
        for entry in pipeline.archive.candidati(now):
            product = entry['prodotto']
            published = datetime.fromisoformat(product['pubblicato_il'])
            if now - published > timedelta(hours=6) or not entry['analisi']['sconto_calcolato']:
                continue
            mid = int(product['telegram_post_url'].rsplit('/',1)[1])
            message = await client.get_messages(channel, ids=mid)
            current = product_from_message(message) if message else None
            if not current or current['asin'] != product['asin'] or not getattr(message, 'photo', None):
                continue
            if any(number(current.get(key)) != number(product.get(key))
                   for key in ('prezzo_attuale','prezzo_precedente')):
                continue
            raw = await client.download_media(message, file=bytes)
            if not raw or len(raw) > 8_000_000:
                continue
            with Image.open(BytesIO(raw)) as photo:
                if photo.width > 4096 or photo.height > 4096:
                    continue
                output = BytesIO()
                photo.convert('RGB').save(output, 'PNG')
                image = output.getvalue()
            if len(image) > 8_000_000:
                continue
            at = published.astimezone(ROME).strftime('%H:%M')
            reference = number(product['prezzo_precedente'])
            discount = round((reference - number(product['prezzo_attuale'])) / reference * 100)
            caption = (f"🐛 La segnalazione del Tarlo delle ultime 6 ore: {product['titolo']}\n"
                       f"Prezzo segnalato alle {at}: {product['prezzo_attuale']} €.\n"
                       f"Riferimento riportato nel post: {product['prezzo_precedente']} € (-{discount:g}%).\n"
                       "Prezzo e disponibilità da ricontrollare: la promozione potrebbe essere cambiata.\n"
                       "Scopri Il Tarlo del Risparmio per altre segnalazioni.\n"
                       "Link affiliati: potremmo ricevere una commissione.\n"
                       f"#Pubblicità #IlTarloDelRisparmio #OfferteAmazon #Tarlo{day.replace('-','')}")
            ident = uuid.uuid4().hex
            checked = datetime.now(timezone.utc)
            payload = {'id':f'tarlo-{day}', 'day':now.astimezone(ROME).date().isoformat(), 'slot':day, 'timezone':'Europe/Rome',
                       'window_start':(now-timedelta(hours=6)).isoformat(), 'window_end':now.isoformat(),
                       'prepared_at':checked.isoformat(),
                       'valid_until':(checked+timedelta(minutes=30)).isoformat(),
                       'product':product, 'caption':caption,
                       'media_path':f'/daily/media/{day}/{ident}.png',
                       'sha256':hashlib.sha256(image).hexdigest(), 'format':'photo',
                       'status':'ready', 'commercial_content':True, 'live_verified':False,
                       'selection_scope':'segnalazioni del canale nelle ultime 6 ore; metriche disponibili',
                       'price_source':'telegram_snapshot', 'price_observed_at':published.isoformat()}
            with pipeline.archive.connection() as conn:
                cur = conn.cursor()
                pipeline.archive.execute(cur, 'UPDATE tarlo_daily_ready SET payload=?, image=? '
                    'WHERE day=? AND owner=? AND payload IS NULL',
                    (json.dumps(payload,ensure_ascii=False),base64.b64encode(image).decode(),day,owner))
                if cur.rowcount != 1:
                    return 'lease_lost'
            pipeline.cleanup()
            return 'ready_reported_price'
        return 'no_recent_offer'
    finally:
        with pipeline.archive.connection() as conn:
            pipeline.archive.execute(conn.cursor(), 'DELETE FROM tarlo_daily_ready '
                'WHERE day=? AND owner=? AND payload IS NULL', (day,owner))


class FreeDaily:
    def __init__(self, pipeline, client, channel, loop, connected):
        self.pipeline, self.client, self.channel = pipeline, client, channel
        self.loop, self.connected = loop, connected
        self.lock = threading.Lock()
        self.future = None
        self.last_attempt = None
        self.state = {'status': 'idle'}

    def request(self):
        with self.lock:
            latest = self.pipeline.latest()
            if latest['status'] == 'ready':
                return {'status': 'ready'}, 200
            if latest['status'] == 'expired':
                # Il contenuto scaduto non viene pubblicato né rigenerato in modo cieco.
                return {'status': 'expired'}, 200
            if self.future and not self.future.done():
                return {'status': 'preparing'}, 202
            if self.last_attempt is not None and time.monotonic() - self.last_attempt < 900:
                return dict(self.state, retry_after_seconds=900), 429
            self.last_attempt = time.monotonic()
            self.state = {'status': 'preparing'}
            self.future = asyncio.run_coroutine_threadsafe(self.prepare(), self.loop)
            return {'status': 'preparing'}, 202

    async def prepare(self):
        try:
            await asyncio.wait_for(self.connected.wait(), timeout=90)
            recovered = await asyncio.wait_for(
                recover_today(self.client, self.pipeline.archive, self.channel), timeout=90)
            result = await asyncio.to_thread(self.pipeline.prepare)
            if result == 'no_verified_offer':
                result = await prepare_reported_offer(self.pipeline, self.client, self.channel)
            self.state = {'status': result, 'recovered_posts': recovered}
            LOG.warning('Preparazione gratuita: %s; post recuperati: %s', result, recovered)
        except Exception:
            self.state = {'status': 'failed'}
            LOG.exception('Preparazione gratuita fallita')


def register_free_routes(app, coordinator):
    from flask import jsonify, request

    @app.post('/daily/prepare')
    def prepare_daily():
        # Nessun input esterno entra nello scraping o nella sessione Telegram.
        if request.query_string or request.content_length or request.headers.get('Origin'):
            return jsonify(error='empty_server_to_server_request_required'), 400
        state, code = coordinator.request()
        response = jsonify(state)
        response.status_code = code
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/daily/status')
    def daily_status():
        response = jsonify(coordinator.state)
        response.headers['Cache-Control'] = 'no-store'
        return response
