"""Bounded, user-authorized correction of existing Telegram photo posts.

No send/delete API and no public mutation endpoint. The channel marker is the
durable reconciliation record; an uncertain edit is never blindly retried.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP
from io import BytesIO
from pathlib import Path

from flask import jsonify
from telegram import InputMediaPhoto
from telegram.helpers import escape_markdown
from amazon_product import money
from daily_dedup import asins_in_message


class PostRepairs:
    def __init__(self, client, bot, channel, scrape, render, tag, request_path):
        self.client, self.bot, self.channel = client, bot, channel
        self.scrape, self.render, self.tag = scrape, render, tag
        self.request_path = Path(request_path)
        self.results = []
        self.status = 'idle'

    def report(self, **result):
        self.results.append(result)
        print('[POST_REPAIR] ' + json.dumps(result, ensure_ascii=False), flush=True)

    async def run(self):
        if not self.request_path.exists():
            return
        request = json.loads(self.request_path.read_text())
        now = datetime.now(timezone.utc)
        start = datetime.fromisoformat(request['start'])
        end = datetime.fromisoformat(request['end'])
        expires = datetime.fromisoformat(request['expires'])
        if not (start.tzinfo and end.tzinfo and expires.tzinfo) or not start < end <= now < expires:
            self.status = 'expired'
            return
        if end-start > __import__('datetime').timedelta(hours=24):
            raise ValueError('Repair scope exceeds 24 hours')
        if not re.fullmatch(r'[a-z0-9-]{1,60}', request['id']):
            raise ValueError('Invalid repair id')
        self.status = 'checking'
        targets = []
        # Finish the read before any edit: access errors cannot imply no posts.
        async for message in self.client.iter_messages(self.channel, limit=None):
            if message.date < start:
                break
            if message.date > end or not getattr(message, 'photo', None):
                continue
            text = message.raw_text or ''
            asins = asins_in_message(message)
            if len(asins) != 1 or '#IlTarloDelRisparmio' not in text:
                continue
            if re.search(r'Riferimento|Prezzo consigliato|Prezzo mediano|coupon', text, re.I):
                continue
            targets.append((message, next(iter(asins))))
        for message, asin in targets:
            marker = '#TarloCorrezione' + request['id'].replace('-', '') + str(message.id)
            current_message = await self.client.get_messages(self.channel, ids=message.id)
            if marker in (current_message.raw_text or ''):
                self.report(status='already_corrected', asin=asin, message_id=message.id)
                continue
            if current_message.raw_text != message.raw_text:
                self.report(status='changed_during_review', asin=asin, message_id=message.id)
                continue
            product = await asyncio.to_thread(self.scrape, asin)
            snapshot = request.get('snapshots', {}).get(asin)
            snapshot_used = False
            if not product or not money(product.get('prezzo_precedente')):
                # A user screenshot is explicitly labelled as a snapshot, never
                # claimed to be a live verification. Exact original title/price
                # and ASIN must match, so it cannot correct another variant.
                if snapshot and snapshot['titolo'] in message.raw_text and (
                        snapshot['prezzo_attuale'] + ' €') in message.raw_text:
                    product = dict(snapshot, asin=asin, tipo_riferimento='non_identificato')
                    snapshot_used = True
                else:
                    self.report(status='no_verified_reference', asin=asin, message_id=message.id)
                    continue
            current, previous = money(product.get('prezzo_attuale')), money(product.get('prezzo_precedente'))
            if not current or not previous or previous <= current or product.get('coupon'):
                self.report(status='not_eligible', asin=asin, message_id=message.id)
                continue
            product['sconto'] = int(((previous-current)/previous*100).quantize(DecimalOne, rounding=ROUND_HALF_UP))
            photo = await asyncio.to_thread(self.render, product, require_image=True)
            labels = {'consigliato':'Prezzo consigliato', 'mediano':'Prezzo mediano',
                      'precedente':'Prezzo precedente', 'piu_basso_30gg':'Prezzo più basso 30 gg (riferimento)'}
            caption = ('🐛 Offerta corretta\n\n🛒 *' + escape_markdown(product['titolo'][:180], version=1) +
                       f"*\n\n💰 *{product['prezzo_attuale']} €*\n" +
                       f"{labels.get(product.get('tipo_riferimento'), 'Riferimento Amazon')}: " +
                       f"{product['prezzo_precedente']} € (-{product['sconto']}%).\n")
            if snapshot_used:
                caption += f"Dati dello screenshot Amazon del {snapshot['source_label']}; prezzo e disponibilità da ricontrollare.\n"
            else:
                caption += 'Prezzo e disponibilità ricontrollati; possono variare.\n'
            caption += (f'👉 [Apri su Amazon](https://www.amazon.it/dp/{asin}?tag={self.tag})\n\n'
                        'Link affiliato.\n🪵 Il Tarlo del Risparmio\n#IlTarloDelRisparmio\n' + marker)
            self.report(status='attempted', asin=asin, message_id=message.id,
                        snapshot=snapshot_used, current=product['prezzo_attuale'],
                        reference=product['prezzo_precedente'], discount=product['sconto'])
            try:
                result = await self.bot.edit_message_media(chat_id=self.channel, message_id=message.id,
                    media=InputMediaPhoto(media=BytesIO(photo), caption=caption, parse_mode='Markdown'))
                if getattr(result, 'message_id', None) != message.id:
                    raise RuntimeError('Edit confirmation missing')
            except Exception as exc:
                # Re-read once, never resend. Telegram edit is in-place, not a
                # second offer; pending outcome remains uncertain if read fails.
                try:
                    check = await self.client.get_messages(self.channel, ids=message.id)
                    confirmed = marker in (check.raw_text or '')
                except Exception:
                    confirmed = False
                self.report(status='corrected' if confirmed else 'uncertain',
                            asin=asin, message_id=message.id, error_type=type(exc).__name__)
                continue
            self.report(status='corrected', asin=asin, message_id=message.id,
                        current=product['prezzo_attuale'], reference=product['prezzo_precedente'],
                        discount=product['sconto'], snapshot=snapshot_used)
        self.status = 'completed'


from decimal import Decimal
DecimalOne = Decimal('1')


def register_routes(app, repairs):
    @app.get('/repairs/status')
    def repair_status():
        response = jsonify(status=repairs.status, results=repairs.results)
        response.headers['Cache-Control'] = 'no-store'
        return response
