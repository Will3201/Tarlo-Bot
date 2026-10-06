"""One bounded, verified category digest during busy periods; no paid services."""
import asyncio
import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from coupon_pricing import prezzo_con_coupon
from prime_event import message_asins, checked_product, REFERENCES
from tarlo_daily import valuta

ROME = ZoneInfo('Europe/Rome')
HOURS = (6, 12, 18, 22)
CHANNEL = '@TarloDelRisparmio'
MIN_OFFERS = 12
MAX_CANDIDATES = 24
MAX_RESULTS = 15
CATEGORIES = [
    ('🐾 Animali', r'lettier|\bgatt[oi]\b|\bcan[ei]\b|petkit|crocchette|cibo.*animali'),
    ('🧴 Cura della persona', r'rasoio|rasatur|oneblade|bodygroom|spazzolino|dentifric|shampoo|bagnoschiuma|deodorante|profum|cosmetic|crema viso|igiene|cura della persona'),
    ('🛒 Spesa e alimenti', r'\briso\b|scotti|biscott|caffè|caffe|cioccolat|barrette|red bull|energy drink|bevanda|alimentari|pasta alimentare|cereali|olio.*oliva|\btonno\b|\blatte\b'),
    ('📱 Tecnologia', r'elettronica|informatica|smartphone|telefono|tablet|computer|notebook|monitor|smart tv|\btv\b|auricolar|cuffi[ae]|bluetooth|usb|power bank|smartwatch|apple watch|ssd|\bram\b|\bmouse\b|tastiera|fotocamer|videocamer|\brgb\b|fire tv'),
    ('🏠 Casa e cucina', r'casa|cucina|aspirapolver|elettrodomestic|friggitr|spremiagrum|candela|stovigli|pentol|detersiv|lavastovigl|biancheria|lenzuol|tovagli|carta igienica'),
    ('🥾 Sport e outdoor', r'sport|outdoor|trekking|escursion|zaino|mammut|scarpon|pesca|bicicletta|fitness|palestra'),
    ('🎲 Giochi e hobby', r'giocattol|giochi|gioco|lego|hot wheels|asmodee|puzzle|modellismo|stampa 3d|filamento')]


def due_marker(now):
    local = now.astimezone(ROME)
    return f'#TarloRecapCategorie{local:%Y%m%d%H}' if local.hour in HOURS else None


def category(product):
    text = ' '.join([product.get('titolo', ''), *product.get('categorie_amazon', [])]).casefold()
    return next((name for name, pattern in CATEGORIES if re.search(pattern, text)), '📦 Altre offerte')


def compact_title(value):
    value = ' '.join(str(value).split())
    if len(value) <= 95:
        return value
    return value[:94].rsplit(' ', 1)[0] + '…'


def format_product(product, tag):
    visual = prezzo_con_coupon(product)
    title = html.escape(compact_title(product['titolo']))
    url = f"https://www.amazon.it/dp/{product['asin']}?tag={quote(tag, safe='')}"
    emoji = category(product).split(' ', 1)[0]
    lines = [f'{emoji} <b>{title}</b>', f'🔥 <b>{visual["prezzo_attuale"]} €</b>']
    if visual.get('coupon_applicato'):
        coupon = product['coupon']
        lines[-1] += ' con coupon'
        lines.append(f'🎟️ Coupon {html.escape(str(coupon["importo"]))}{html.escape(coupon["unita"])}: '
                     f'attiva la casella e verifica i requisiti. Senza coupon: {product["prezzo_attuale"]} €.')
    elif product.get('prezzo_precedente') and product.get('tipo_riferimento') in REFERENCES:
        lines[-1] += f' (−{product["sconto"]}%)'
        lines.append(f'📊 {REFERENCES[product["tipo_riferimento"]].capitalize()}: {product["prezzo_precedente"]} €')
    # The destination is visible as well as clickable; never hide it in the title.
    lines.append(f'👉 {html.escape(url)}')
    return '\n'.join(lines)


def recap_text(products, marker, start, checked_at, tag):
    header = ('🐛 <b>Le offerte del Tarlo, per categoria</b>\n'
              f'Selezione dalle segnalazioni delle {start.astimezone(ROME):%H:%M}–{checked_at.astimezone(ROME):%H:%M}. '
              f'Ricontrollata su Amazon il {checked_at.astimezone(ROME):%d/%m} alle {checked_at.astimezone(ROME):%H:%M}.')
    footer = ('\n\nPrezzi, coupon e disponibilità possono variare: ricontrolla il totale su Amazon. '
              'Le offerte riservate a Prime richiedono un abbonamento idoneo.\n'
              'Link affiliati: potremmo ricevere una commissione.\n'
              '👉 https://t.me/TarloDelRisparmio\n' + marker)
    groups = defaultdict(list)
    for product in products:
        groups[category(product)].append(product)
    lines, selected = [], []
    for name, group in groups.items():
        heading_added = False
        for product in group[:3]:
            if len(selected) >= MAX_RESULTS:
                break
            addition = (['', '', '<b>' + name + '</b>'] if not heading_added else []) + ['', format_product(product, tag)]
            candidate = header + '\n'.join(lines + addition) + footer
            # Conservative raw HTML limit, below Telegram's 4096 text limit.
            if len(candidate) > 3900:
                continue
            lines += addition
            selected.append(product)
            heading_added = True
    return header + '\n'.join(lines) + footer, selected


class CategoryRecaps:
    def __init__(self, client, bot, archive, scraper, channel, tag, min_offers=MIN_OFFERS):
        self.client, self.bot, self.archive, self.scraper = client, bot, archive, scraper
        self.channel, self.tag, self.min_offers = channel, tag, min_offers
        self.last = None
        with archive.connection() as conn:
            conn.cursor().execute('CREATE TABLE IF NOT EXISTS tarlo_category_recaps '
                                  '(marker TEXT PRIMARY KEY, status TEXT NOT NULL, details TEXT NOT NULL)')

    def state(self, marker):
        with self.archive.connection() as conn:
            cur = conn.cursor()
            self.archive.execute(cur, 'SELECT status,details FROM tarlo_category_recaps WHERE marker=?', (marker,))
            row = cur.fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def record(self, marker, status, details):
        with self.archive.connection() as conn:
            self.archive.execute(conn.cursor(), 'INSERT INTO tarlo_category_recaps (marker,status,details) VALUES (?,?,?) '
                                 'ON CONFLICT (marker) DO UPDATE SET status=excluded.status,details=excluded.details',
                                 (marker, status, json.dumps(details, ensure_ascii=False)))
        self.last = {'marker': marker, 'status': status, **details}
        print('[CATEGORY_RECAP] ' + json.dumps(self.last, ensure_ascii=False), flush=True)

    async def history(self, start):
        messages = []
        async for message in self.client.iter_messages(self.channel, limit=4000):
            if message.date.astimezone(ROME) < start:
                return messages
            messages.append(message)
        if len(messages) == 4000:
            raise RuntimeError('Storico incompleto: non inviare senza copertura delle ultime sei ore')
        return messages

    async def send_once(self, marker, text, history, details):
        existing = next((m for m in history if marker in (m.raw_text or '').split()), None)
        if existing:
            self.record(marker, 'published', {**details, 'message_id': existing.id,
                        'published_at': existing.date.isoformat()})
            return
        if self.state(marker):
            return
        self.record(marker, 'attempted', details)
        try:
            result = await self.bot.send_message(chat_id=self.channel, text=text, parse_mode='HTML',
                                                 disable_web_page_preview=True)
            self.record(marker, 'published', {**details, 'message_id': result.message_id,
                        'published_at': datetime.now(ROME).isoformat()})
        except Exception as exc:
            self.last = {'marker': marker, 'status': 'attempted', 'reason': type(exc).__name__}
            print(f'[CATEGORY_RECAP] {marker}: esito incerto ({type(exc).__name__}), nessun reinvio', flush=True)

    async def tick(self, now):
        marker = due_marker(now)
        if marker is None:
            return
        if self.channel.lower() != CHANNEL.lower():
            raise RuntimeError('Destinazione non autorizzata')
        previous = self.state(marker)
        if previous and previous[0] != 'attempted':
            return
        start = now - timedelta(hours=6)
        messages = await self.history(start)
        existing = next((m for m in messages if marker in (m.raw_text or '').split()), None)
        if existing:
            self.record(marker, 'published', {**(previous[1] if previous else {}), 'message_id': existing.id,
                        'published_at': existing.date.isoformat()})
            return
        if previous:
            return  # Ambiguous send is never retried, even after restart.
        reported = {}
        for message in messages:
            if not start <= message.date.astimezone(ROME) <= now:
                continue
            if re.search(r'#Tarlo(?:RecapCategorie|Prime)', message.raw_text or ''):
                continue  # A recap is not a fresh set of source offers.
            for asin in message_asins(message):
                reported.setdefault(asin, message)
        if len(reported) < self.min_offers:
            self.record(marker, 'low_volume', {'at': now.isoformat(), 'reported_count': len(reported),
                                              'threshold': self.min_offers})
            return
        # Recover publication data after a free-tier restart; rank archived
        # metrics when available. Source prices never become current prices.
        from free_daily import product_from_message
        snapshots = {asin: product_from_message(message) for asin, message in reported.items()}
        archived = self.archive.candidati(now)
        for entry in archived:
            p = entry['prodotto']
            if p['asin'] in reported:
                snapshots[p['asin']] = p
        ranked = sorted(reported, key=lambda asin: -(valuta(snapshots.get(asin) or {}) or {}).get('punteggio', 0))
        buckets = defaultdict(list)
        for asin in ranked:
            buckets[category(snapshots.get(asin) or {})].append(asin)
        candidates = []
        while len(candidates) < MAX_CANDIDATES and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(candidates) < MAX_CANDIDATES:
                    candidates.append(bucket.pop(0))
        products = []
        for asin in candidates:
            product = await asyncio.to_thread(self.scraper, asin, strict=True)
            if checked_product(product, asin):
                products.append(product)
        finished = datetime.now(ROME)
        products.sort(key=lambda p: -valuta(p)['punteggio'])
        # Bound quote age; do not publish a price captured early in a long run.
        products = [p for p in products if p.get('verificato_il') and
                    finished - timedelta(minutes=5) <= datetime.fromisoformat(p['verificato_il']) <= finished]
        if due_marker(finished) != marker or len(products) < 3:
            self.record(marker, 'no_verified_selection', {'reported_count': len(reported),
                                                         'verified_count': len(products)})
            return
        text, selected = recap_text(products, marker, start, finished, self.tag)
        if len(selected) < 3:
            self.record(marker, 'no_verified_selection', {'reason': 'meno di tre offerte leggibili'})
            return
        await self.send_once(marker, text, messages, {'asins': [p['asin'] for p in selected],
            'categories': list(dict.fromkeys(category(p) for p in selected)),
            'reported_count': len(reported), 'candidate_count': len(candidates),
            'verified_count': len(selected), 'window_start': start.isoformat(),
            'window_end': now.isoformat(), 'checked_at': finished.isoformat(),
            'text_sha256': hashlib.sha256(text.encode()).hexdigest()})

    async def run(self):
        while True:
            try:
                await self.tick(datetime.now(ROME))
            except Exception as exc:
                print(f'[CATEGORY_RECAP] Controllo bloccato: {type(exc).__name__}; nessun invio senza verifica', flush=True)
            await asyncio.sleep(60)


def register_routes(app, recaps):
    from flask import jsonify

    @app.get('/recaps/status')
    def recap_status():
        last = recaps.last or {}
        data = {k: last[k] for k in ('marker', 'status', 'at', 'message_id', 'published_at',
                                    'reported_count', 'verified_count', 'categories', 'reason') if k in last}
        response = jsonify(enabled=True, timezone='Europe/Rome', hours=list(HOURS),
                           min_distinct_offers=recaps.min_offers, last=data)
        response.headers['Cache-Control'] = 'no-store'
        return response
