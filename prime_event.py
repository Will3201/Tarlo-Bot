"""Speciale Telegram temporaneo 6-7 ottobre 2026. Nessun acquisto di pubblicità."""
import asyncio
import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from tarlo_daily import number, valuta

ROME = ZoneInfo('Europe/Rome')
START = datetime(2026, 10, 6, tzinfo=ROME)
END = datetime(2026, 10, 8, tzinfo=ROME)
SOURCE = 'https://www.aboutamazon.it/notizie/company-news/preparati-risparmiare-festa-offerte-prime-6-7-ottobre'
CHANNEL = '@TarloDelRisparmio'
WELCOME = '#TarloPrime202610Benvenuto'
REFERENCES = {'consigliato': 'prezzo consigliato', 'mediano': 'prezzo mediano',
              'precedente': 'prezzo precedente', 'piu_basso_30gg': 'prezzo più basso dei 30 giorni'}


def event_active(now):
    return START <= now.astimezone(ROME) < END


def due_marker(now):
    """Non recuperare riepiloghi vecchi: finestra di invio di un'ora."""
    local = now.astimezone(ROME)
    if event_active(local) and local.hour in (12, 20):
        return f'#TarloPrime{local:%Y%m%d%H}'
    return None


def message_asins(message):
    pieces = [message.raw_text or '']
    pieces.extend(getattr(e, 'url', '') for e in (message.entities or []))
    return list(dict.fromkeys(re.findall(r'/dp/([A-Z0-9]{10})(?=[/?\s)]|$)', ' '.join(pieces))))


def checked_product(product, asin):
    if not product or product.get('asin') != asin or product.get('disponibile') is not True:
        return False
    price = number(product.get('prezzo_attuale'))
    return bool(price and price > 0 and product.get('titolo') and valuta(product))


def recap_text(products, marker, checked_at, tag):
    lines = ['🐛 <b>La selezione del Tarlo durante la Festa delle Offerte Prime</b>',
             f'Ricontrollata su Amazon alle {checked_at.astimezone(ROME):%H:%M} del {checked_at.astimezone(ROME):%d/%m}.', '']
    for index, product in enumerate(products, 1):
        title = html.escape(product['titolo'][:110])
        price = html.escape(str(product['prezzo_attuale']))
        url = f"https://www.amazon.it/dp/{product['asin']}?tag={quote(tag, safe='')}"
        lines.append(f'{index}. <a href="{url}">{title}</a> — <b>{price} €</b>')
        reference = REFERENCES.get(product.get('tipo_riferimento'))
        ref = number(product.get('prezzo_precedente'))
        current = number(product.get('prezzo_attuale'))
        if reference and ref and ref > current:
            discount = round((1 - current / ref) * 100)
            lines.append(f"   −{discount}% sul {reference} di {html.escape(str(product['prezzo_precedente']))} €.")
    lines.extend(['', 'Selezione tra le segnalazioni del canale nelle ultime 6 ore, ordinata per sconto, valutazioni e acquisti quando disponibili.',
                  'Le offerte riservate a Prime richiedono un abbonamento idoneo. Prezzi, coupon e disponibilità possono cambiare: verifica il totale su Amazon.',
                  'Link affiliati: potremmo ricevere una commissione.',
                  '👉 Condividi il canale: https://t.me/TarloDelRisparmio', marker])
    return '\n'.join(lines)


class PrimeCampaign:
    def __init__(self, client, bot, archive, scraper, channel, tag):
        self.client, self.bot, self.archive, self.scraper = client, bot, archive, scraper
        self.channel, self.tag = channel, tag
        self.metrics_slots = set()
        with archive.connection() as conn:
            conn.cursor().execute('CREATE TABLE IF NOT EXISTS tarlo_prime_campaign '
                                  '(marker TEXT PRIMARY KEY, status TEXT NOT NULL, details TEXT NOT NULL)')

    def record(self, marker, status, details):
        with self.archive.connection() as conn:
            self.archive.execute(conn.cursor(), 'INSERT INTO tarlo_prime_campaign (marker,status,details) VALUES (?,?,?) '
                                 'ON CONFLICT (marker) DO UPDATE SET status=excluded.status,details=excluded.details',
                                 (marker, status, json.dumps(details, ensure_ascii=False)))

    def state(self, marker):
        with self.archive.connection() as conn:
            cur = conn.cursor()
            self.archive.execute(cur, 'SELECT status,details FROM tarlo_prime_campaign WHERE marker=?', (marker,))
            row = cur.fetchone()
            return (row[0], json.loads(row[1])) if row else None

    async def history(self):
        messages = []
        async for message in self.client.iter_messages(self.channel, limit=4000):
            if message.date.astimezone(ROME) < START:
                return messages
            messages.append(message)
        if len(messages) == 4000:
            raise RuntimeError('Storico evento oltre il limite: non inviare senza riconciliazione completa')
        return messages

    async def send_once(self, marker, text, history, details=None, welcome=False):
        existing = next((m for m in history if marker in (m.raw_text or '').split()), None)
        old = self.state(marker)
        if existing:
            if not old or old[0] != 'published':
                self.record(marker, 'published', {**(old[1] if old else {}), 'message_id': existing.id})
            return
        if old:
            # Anche attempted/no_offer/error è definitivo: mai reinviare esiti incerti.
            return
        details = details or {}
        self.record(marker, 'attempted', details)
        try:
            keyboard = None
            if welcome:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                share = 'https://t.me/share/url?url=https%3A%2F%2Ft.me%2FTarloDelRisparmio&text=' + quote('Il Tarlo del Risparmio: offerte e selezioni per la Festa delle Offerte Prime del 6 e 7 ottobre.')
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('🐛 Condividi il canale', url=share)]])
            sent = await self.bot.send_message(chat_id=self.channel, text=text, parse_mode='HTML',
                                               disable_web_page_preview=True, reply_markup=keyboard)
            details.update(message_id=sent.message_id, published_at=datetime.now(ROME).isoformat())
            self.record(marker, 'published', details)
            print('[PRIME_CAMPAIGN] ' + json.dumps({'marker': marker, 'status': 'published', **details}, ensure_ascii=False), flush=True)
            if welcome:
                try:
                    await self.bot.pin_chat_message(chat_id=self.channel, message_id=sent.message_id, disable_notification=True)
                    print('[PRIME_CAMPAIGN] Benvenuto fissato nel canale', flush=True)
                except Exception as exc:
                    print(f'[PRIME_CAMPAIGN] Pubblicato ma pin non riuscito: {type(exc).__name__}', flush=True)
        except Exception as exc:
            print(f'[PRIME_CAMPAIGN] {marker}: invio incerto o fallito ({type(exc).__name__}); nessun reinvio', flush=True)

    async def tick(self, now):
        if not event_active(now):
            return
        if self.channel.lower() != CHANNEL.lower():
            raise RuntimeError('Destinazione diversa dal canale autorizzato')
        history = await self.history()
        if not self.state(WELCOME) and not any(WELCOME in (m.raw_text or '').split() for m in history):
            members = await self.bot.get_chat_member_count(self.channel)
            text = ('🐛 <b>Speciale Festa delle Offerte Prime · 6 e 7 ottobre</b>\n\n'
                    'Le offerte del Tarlo sono qui! Durante questi due giorni aggiungiamo selezioni alle 12 e alle 20, quando troviamo prodotti ricontrollabili su Amazon.\n\n'
                    '🔎 Prima di comprare controlla quantità, venditore, eventuale coupon e prezzo finale. Uno sconto elevato non dimostra un errore di prezzo o un minimo storico.\n'
                    'Le offerte riservate a Prime richiedono un abbonamento idoneo. Prezzi e disponibilità possono cambiare.\n\n'
                    '❤️ Conosci qualcuno che sta cercando un acquisto? Condividi il canale: https://t.me/TarloDelRisparmio\n'
                    'Link affiliati: potremmo ricevere una commissione.\n\n' + WELCOME)
            await self.send_once(WELCOME, text, history, {'baseline_members': members, 'source': SOURCE}, welcome=True)
        marker = due_marker(now)
        if marker and not self.state(marker) and not any(marker in (m.raw_text or '').split() for m in history):
            asins = []
            start = now - timedelta(hours=6)
            for message in history:
                if start <= message.date.astimezone(ROME) <= now and '#TarloPrime' not in (message.raw_text or ''):
                    asins.extend(message_asins(message))
            products = []
            for asin in list(dict.fromkeys(asins))[:10]:
                product = await asyncio.to_thread(self.scraper, asin, strict=True)
                if checked_product(product, asin):
                    products.append(product)
            products.sort(key=lambda p: valuta(p)['punteggio'], reverse=True)
            final = []
            for product in products[:5]:
                check = await asyncio.to_thread(self.scraper, product['asin'], strict=True)
                if checked_product(check, product['asin']) and number(check['prezzo_attuale']) == number(product['prezzo_attuale']):
                    final.append(check)
            finished = datetime.now(ROME)
            if final and due_marker(finished) == marker:
                text = recap_text(final, marker, finished, self.tag)
                await self.send_once(marker, text, history, {'asins': [p['asin'] for p in final], 'checked_at': finished.isoformat(),
                                    'window_start': start.isoformat(), 'window_end': now.isoformat(),
                                    'text_sha256': hashlib.sha256(text.encode()).hexdigest()})
            else:
                self.record(marker, 'no_verified_offer', {'candidate_count': len(set(asins))})
                print(f'[PRIME_CAMPAIGN] {marker}: nessuna selezione verificabile o fascia terminata', flush=True)
        metric_slot = f'{now:%Y%m%d%H}'
        if now.hour in (0, 9, 21) and metric_slot not in self.metrics_slots:
            members = await self.bot.get_chat_member_count(self.channel)
            baseline = self.state(WELCOME)
            baseline_members = baseline[1].get('baseline_members') if baseline else None
            special = [m for m in history if '#TarloPrime' in (m.raw_text or '')]
            print('[PRIME_STATS] ' + json.dumps({'at': now.isoformat(), 'members': members,
                'baseline_members': baseline_members,
                'net_member_change': members - baseline_members if baseline_members is not None else None,
                'posts': [{'id': m.id, 'views': getattr(m, 'views', None)} for m in special],
                'attribution': 'variazione netta totale, non iscrizioni attribuite alla campagna'}, ensure_ascii=False), flush=True)
            self.metrics_slots.add(metric_slot)

    async def run(self):
        while datetime.now(ROME) < END:
            try:
                await self.tick(datetime.now(ROME))
            except Exception as exc:
                print(f'[PRIME_CAMPAIGN] Controllo bloccato: {type(exc).__name__}; nessun invio senza verifica', flush=True)
            await asyncio.sleep(60)

