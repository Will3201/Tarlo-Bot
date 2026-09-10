"""Preparazione su richiesta usando il canale pubblico come archivio gratuito.

Non pubblica messaggi. Il POST accetta solo un comando fisso, senza URL o prodotti
forniti dal chiamante, e limita le preparazioni a una ogni 15 minuti per processo.
"""
import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timezone

from tarlo_daily import ROME, number

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
    start = now.astimezone(ROME).replace(hour=0, minute=0, second=0, microsecond=0)
    count = 0
    async for message in client.iter_messages(channel, limit=500):
        if message.date < start:
            break
        if message.date > now:
            continue
        product = product_from_message(message)
        if product:
            await asyncio.to_thread(archive.registra, product, message.id, channel, message.date)
            count += 1
    return count


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
