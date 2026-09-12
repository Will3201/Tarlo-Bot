"""Prepara una creatività al giorno. Pubblicazione gestita da Metricool.

Il feed contiene solo il post pubblicitario pronto, nessun dato di accesso.
Gli endpoint pubblici sono read-only: non attivano scraping o generazioni.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO

from tarlo_daily import ROME, number, valuta

LOG = logging.getLogger(__name__)


def publication_slot(now):
    local = now.astimezone(ROME)
    # Orari italiani 01, 07, 13, 19; prima dell'una appartiene alla fascia precedente.
    if local.hour < 1:
        local -= timedelta(days=1)
        hour = 19
    else:
        hour = 1 + ((local.hour - 1) // 6) * 6
    return f"{local.date().isoformat()}-{hour:02d}"


class DailyPipeline:
    def __init__(self, archive, scrape, render, hour='18:00'):
        self.archive, self.scrape, self.render = archive, scrape, render
        self.hour, self.minute = (int(x) for x in hour.split(':'))
        if not (0 <= self.hour < 24 and 0 <= self.minute < 60):
            raise ValueError('DAILY_PREPARE_TIME deve essere HH:MM')
        with archive.connection() as conn:
            conn.cursor().execute('CREATE TABLE IF NOT EXISTS tarlo_daily_ready ('
                                  'day TEXT PRIMARY KEY, owner TEXT NOT NULL, '
                                  'lease_until TEXT NOT NULL, payload TEXT, image TEXT)')

    def claim(self, day, now):
        owner = uuid.uuid4().hex
        lease = (now + timedelta(minutes=15)).isoformat()
        with self.archive.connection() as conn:
            cur = conn.cursor()
            self.archive.execute(cur, 'INSERT INTO tarlo_daily_ready '
                                 '(day, owner, lease_until) VALUES (?, ?, ?) '
                                 'ON CONFLICT (day) DO NOTHING', (day, owner, lease))
            self.archive.execute(cur, 'UPDATE tarlo_daily_ready SET owner=?, lease_until=? '
                                 'WHERE day=? AND payload IS NULL AND lease_until < ?',
                                 (owner, lease, day, now.isoformat()))
            self.archive.execute(cur, 'SELECT owner, payload FROM tarlo_daily_ready WHERE day=?', (day,))
            row = cur.fetchone()
        return owner if row and row[0] == owner and row[1] is None else None

    def prepare(self, now=None):
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        day = publication_slot(now)
        owner = self.claim(day, now)
        if owner is None:
            return 'already_claimed_or_ready'
        try:
            fresh = []
            # Le prime dieci sono una shortlist: non tutte le offerte del giorno
            # vengono riverificate. Si ricalcola il punteggio con dati freschi.
            for entry in self.archive.candidati(now)[:10]:
                old = entry['prodotto']
                try:
                    product = self.scrape(old['asin'], strict=True)
                except Exception:
                    LOG.exception('Ricontrollo fallito per %s', old['asin'])
                    continue
                if not product or product.get('disponibile') is not True:
                    continue
                score = valuta(product)
                if not score or not score['sconto_calcolato'] or not product.get('immagine_url'):
                    continue
                if product.get('tipo_riferimento') not in ('consigliato', 'mediano', 'piu_basso_30gg', 'precedente'):
                    continue
                product['telegram_post_url'] = old['telegram_post_url']
                product['pubblicato_il'] = old['pubblicato_il']
                fresh.append((score['punteggio'], product))
            fresh.sort(key=lambda item: (-item[0], item[1]['asin']))
            for _, product in fresh:
                # Ultimo ricontrollo dopo la shortlist, prima di fissare il prezzo.
                final = self.scrape(product['asin'], strict=True)
                if not final or final.get('disponibile') is not True:
                    continue
                if any(final.get(k) != product.get(k) for k in
                       ('prezzo_attuale', 'prezzo_precedente', 'tipo_riferimento')):
                    continue
                try:
                    image = self.render(product)
                except Exception:
                    LOG.exception('Creatività fallita per %s', product['asin'])
                    continue
                if not image or len(image) > 8_000_000:
                    continue
                checked = datetime.now(timezone.utc)
                ident = uuid.uuid4().hex
                payload = {
                    'id': f'tarlo-{day}', 'day': now.astimezone(ROME).date().isoformat(), 'slot': day, 'timezone': 'Europe/Rome',
                    'window_start': (now-timedelta(hours=6)).isoformat(), 'window_end': now.isoformat(),
                    'prepared_at': checked.isoformat(),
                    'valid_until': (checked + timedelta(minutes=30)).isoformat(),
                    'product': product, 'caption': caption(product, day),
                    'media_path': f'/daily/media/{day}/{ident}.png',
                    'sha256': hashlib.sha256(image).hexdigest(),
                    'format': 'photo', 'status': 'ready',
                    'selection_scope': 'migliore tra i candidati delle ultime 6 ore ricontrollati',
                    'commercial_content': True,
                }
                with self.archive.connection() as conn:
                    cur = conn.cursor()
                    self.archive.execute(cur, 'UPDATE tarlo_daily_ready SET payload=?, image=? '
                                         'WHERE day=? AND owner=? AND payload IS NULL',
                                         (json.dumps(payload, ensure_ascii=False),
                                          base64.b64encode(image).decode(), day, owner))
                    if cur.rowcount != 1:
                        return 'lease_lost'
                self.cleanup()
                return 'ready'
            return 'no_verified_offer'
        finally:
            with self.archive.connection() as conn:
                self.archive.execute(conn.cursor(), 'DELETE FROM tarlo_daily_ready '
                                     'WHERE day=? AND owner=? AND payload IS NULL', (day, owner))

    def latest(self, now=None):
        now = now or datetime.now(timezone.utc)
        day = publication_slot(now)
        with self.archive.connection() as conn:
            cur = conn.cursor()
            self.archive.execute(cur, 'SELECT payload FROM tarlo_daily_ready WHERE day=?', (day,))
            row = cur.fetchone()
        if not row or not row[0]:
            return {'status': 'not_ready', 'day': day}
        payload = json.loads(row[0])
        if datetime.fromisoformat(payload['valid_until']) < now:
            return {'status': 'expired', 'day': day, 'id': payload['id']}
        return payload

    def media(self, day, ident):
        with self.archive.connection() as conn:
            cur = conn.cursor()
            self.archive.execute(cur, 'SELECT payload, image FROM tarlo_daily_ready WHERE day=?', (day,))
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        payload = json.loads(row[0])
        if payload['media_path'] != f'/daily/media/{day}/{ident}.png':
            return None
        return base64.b64decode(row[1])

    def cleanup(self):
        cutoff = (datetime.now(ROME) - timedelta(days=7)).date().isoformat()
        with self.archive.connection() as conn:
            self.archive.execute(conn.cursor(), 'DELETE FROM tarlo_daily_ready WHERE day < ?', (cutoff,))
        self.archive.elimina_vecchie()

    async def run(self):
        while True:
            local = datetime.now(ROME)
            target = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
            # Recupero breve dopo un riavvio; niente post serali con prezzi vecchi.
            if target <= local <= target + timedelta(minutes=20):
                try:
                    result = await asyncio.to_thread(self.prepare)
                    LOG.info('Preparazione giornaliera: %s', result)
                except Exception:
                    LOG.exception('Preparazione giornaliera fallita')
                await asyncio.sleep(300)
            else:
                await asyncio.sleep(30)


def caption(p, day):
    labels = {'consigliato': 'prezzo consigliato', 'mediano': 'prezzo mediano',
              'piu_basso_30gg': 'prezzo più basso degli ultimi 30 giorni',
              'precedente': 'prezzo precedente'}
    reference = labels[p['tipo_riferimento']]
    text = (f"La scelta del Tarlo delle ultime 6 ore: {p['titolo']}\n"
            f"{p['prezzo_attuale']} € — sconto {p['sconto']}% rispetto al {reference} "
            f"di {p['prezzo_precedente']} €.\n")
    if p.get('stelle') is not None and p.get('numero_recensioni'):
        text += f"Valutazione {p['stelle']}/5 su {p['numero_recensioni']} valutazioni Amazon.\n"
    text += ('Prezzo rilevato alla preparazione: può variare.\n'
             'Altre offerte su Il Tarlo del Risparmio. Link affiliati: potremmo ricevere una commissione.\n'
             f"#IlTarloDelRisparmio #OfferteAmazon #Tarlo{day.replace('-', '')}")
    return text


def register_routes(app, pipeline):
    from flask import abort, jsonify, make_response

    @app.get('/daily/latest')
    def daily_latest():
        response = jsonify(pipeline.latest())
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/daily/media/<day>/<ident>.png')
    def daily_media(day, ident):
        data = pipeline.media(day, ident)
        if data is None:
            abort(404)
        response = make_response(data)
        response.headers['Content-Type'] = 'image/png'
        response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        return response
