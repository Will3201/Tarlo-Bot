"""Archivio e selezione candidati TikTok. Non pubblica né programma post.

Compatibile con il bot fornito dall'utente. Solo libreria standard,
più psycopg2 quando DATABASE_URL è impostata; Flask per l'endpoint opzionale.
"""
import hmac
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace('€', '').replace(' ', '')
        if ',' in text:
            text = text.replace('.', '').replace(',', '.')
        value = Decimal(text)
        return float(value) if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def estrai_metriche(soup):
    """Solo elementi del prodotto; selettori best effort, assenza = None.

    Gli acquisti mensili sono un limite inferiore indicativo, non vendite
    giornaliere esatte. Nessun tentativo di aggirare CAPTCHA o blocchi.
    """
    out = {'stelle': None, 'numero_recensioni': None,
           'acquisti_mese_min': None, 'acquisti_mese_testo': None}
    rating = soup.select_one('#acrPopover')
    if rating is not None:
        text = rating.get('title') or rating.get_text(' ', strip=True)
        match = re.search(r'(\d[,.]\d)\s*(?:su|out of)\s*5', text, re.I)
        if match:
            out['stelle'] = number(match.group(1))
    reviews = soup.select_one('#acrCustomerReviewText')
    if reviews is not None:
        match = re.search(r'\d[\d.,\s\xa0]*', reviews.get_text(' ', strip=True))
        if match:
            out['numero_recensioni'] = int(re.sub(r'\D', '', match.group()))
    sales = soup.select_one('#socialProofingAsinFaceout_feature_div')
    if sales is not None:
        text = sales.get_text(' ', strip=True)
        match = re.search(r'(\d[\d.,]*)\s*(mila|k)?\s*\+?\s*(?:acquist|bought)', text, re.I)
        if match and re.search(r'(mese|month)', text, re.I):
            raw = match.group(1)
            count = number(raw) if match.group(2) else int(re.sub(r'\D', '', raw))
            if count is not None:
                out['acquisti_mese_min'] = int(count * (1000 if match.group(2) else 1))
                out['acquisti_mese_testo'] = text[:300]
    return out


def valuta(p):
    """Pesi iniziali espliciti, da calibrare. Non certifica la convenienza.

    50 punti sconto sul riferimento dichiarato, 20 valutazione con prior,
    15 volume recensioni, 15 acquisti mensili. Dati mancanti: 0 punti e
    copertura ridotta, mai numeri inventati o pesi redistribuiti.
    """
    price, reference = number(p.get('prezzo_attuale')), number(p.get('prezzo_precedente'))
    if price is None or price <= 0:
        return None
    discount = (1 - price / reference) * 100 if reference and reference > price else None
    rating, reviews = number(p.get('stelle')), number(p.get('numero_recensioni'))
    sales = number(p.get('acquisti_mese_min'))
    scores, missing = {}, []
    if discount is not None:
        scores['sconto'] = min(discount / 70, 1) * 50
    else:
        missing.append('prezzo_di_riferimento')
    if rating is not None and 0 <= rating <= 5 and reviews is not None and reviews > 0:
        adjusted = (rating * reviews + 4 * 100) / (reviews + 100)
        scores['valutazione'] = adjusted / 5 * 20
    else:
        missing.append('valutazione_con_recensioni')
    if reviews is not None and reviews >= 0:
        scores['recensioni'] = min(math.log10(1 + reviews) / 4, 1) * 15
    else:
        missing.append('numero_recensioni')
    if sales is not None and sales >= 0:
        scores['acquisti_mese'] = min(math.log10(1 + sales) / 4, 1) * 15
    else:
        missing.append('acquisti_mese')
    return {'punteggio': round(sum(scores.values()), 3),
            'componenti': scores, 'dati_mancanti': missing,
            'sconto_calcolato': round(discount, 2) if discount is not None else None,
            'risparmio_euro': round(reference - price, 2) if discount is not None else None,
            'verifica_prima_di_pubblicare': True}


class ArchivioOfferte:
    def __init__(self, db_path=None, database_url=None):
        self.db_path = str(db_path or Path(__file__).with_name('offerte_daily.db'))
        self.database_url = os.getenv('DATABASE_URL', '') if database_url is None else database_url
        if self.database_url:
            import psycopg2  # Errore esplicito: niente fallback silenzioso a SQLite.
            self.pg_connect = psycopg2.connect
        self.init_db()

    @contextmanager
    def connection(self):
        conn = self.pg_connect(self.database_url) if self.database_url else sqlite3.connect(self.db_path, timeout=30)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, cur, sql, values=()):
        cur.execute(sql.replace('?', '%s') if self.database_url else sql, values)

    def init_db(self):
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute('CREATE TABLE IF NOT EXISTS tarlo_offerte_daily ('
                        'message_key TEXT PRIMARY KEY, asin TEXT NOT NULL, '
                        'published_at TEXT NOT NULL, payload TEXT NOT NULL)')
            cur.execute('CREATE INDEX IF NOT EXISTS tarlo_daily_date ON tarlo_offerte_daily (published_at)')

    def registra(self, prodotto, message_id, channel='TarloDelRisparmio', when=None):
        if not re.fullmatch(r'[A-Z0-9]{10}', str(prodotto.get('asin', ''))):
            raise ValueError('ASIN non valido')
        if valuta(prodotto) is None:
            raise ValueError('Prezzo non valido')
        when = when or datetime.now(timezone.utc)
        if when.tzinfo is None:
            raise ValueError('Usare una data con timezone')
        timestamp = when.astimezone(timezone.utc).isoformat()
        channel = channel.lstrip('@')
        p = dict(prodotto, telegram_post_url=f'https://t.me/{channel}/{int(message_id)}',
                 pubblicato_il=timestamp)
        with self.connection() as conn:
            self.execute(conn.cursor(), 'INSERT INTO tarlo_offerte_daily '
                         '(message_key, asin, published_at, payload) VALUES (?, ?, ?, ?) '
                         'ON CONFLICT (message_key) DO NOTHING',
                         (f'{channel}:{int(message_id)}', p['asin'], timestamp,
                          json.dumps(p, ensure_ascii=False, allow_nan=False)))

    def candidati(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('Usare una data con timezone')
        start = now.astimezone(ROME).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.connection() as conn:
            cur = conn.cursor()
            self.execute(cur, 'SELECT payload FROM tarlo_offerte_daily '
                         'WHERE published_at >= ? AND published_at <= ? ORDER BY published_at DESC, message_key DESC',
                         (start.astimezone(timezone.utc).isoformat(), now.astimezone(timezone.utc).isoformat()))
            rows = cur.fetchall()
        candidates, seen = [], set()
        for (payload,) in rows:
            p = json.loads(payload)
            if p['asin'] in seen:
                continue
            seen.add(p['asin'])  # Ultima segnalazione del prodotto nel giorno.
            score = valuta(p)
            if score:
                candidates.append(dict(prodotto=p, analisi=score))
        return sorted(candidates, key=lambda x: (-x['analisi']['punteggio'], x['prodotto']['asin']))

    def elimina_vecchie(self, days=30):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connection() as conn:
            self.execute(conn.cursor(), 'DELETE FROM tarlo_offerte_daily WHERE published_at < ?', (cutoff,))


def aggiungi_endpoint(app, archivio):
    """Feed privato. Non include credenziali, non pubblica e non riscrape Amazon."""
    from flask import abort, jsonify, request

    @app.get('/api/tarlo/candidati')
    def candidati_tarlo():
        token = os.getenv('TARLO_FEED_TOKEN', '')
        if not token:
            abort(503)
        if not hmac.compare_digest(request.headers.get('Authorization', ''), f'Bearer {token}'):
            abort(401)
        rows = archivio.candidati()
        response = jsonify(timezone='Europe/Rome', finestra='oggi fino a ora',
                           totale=len(rows), candidati=rows[:20],
                           stato='candidati_da_ricontrollare', pubblicazione_automatica=False)
        response.headers['Cache-Control'] = 'no-store'
        return response
