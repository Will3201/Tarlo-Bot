"""Il canale Telegram è lo storico durevole degli ASIN pubblicati oggi."""
import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')


def asins_in_message(message):
    text = getattr(message, 'raw_text', '') or ''
    urls = [getattr(e, 'url', '') or '' for e in (getattr(message, 'entities', None) or [])]
    # I post del bot contengono link diretti /dp/, anche come hyperlink nascosti.
    return {m.upper() for m in re.findall(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?![A-Z0-9])',
                                         text+' '+ ' '.join(urls), re.I)}


class DailyDedup:
    def __init__(self, client, channel, clock=None):
        self.client, self.channel = client, channel
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = asyncio.Lock()
        self.day = None
        self.last_id = 0
        self.seen = set()

    async def refresh(self):
        now = self.clock().astimezone(ROME)
        day = now.date()
        same_day = day == self.day
        seen = set(self.seen) if same_day else set()
        last_id = self.last_id if same_day else 0
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Nessun limite arbitrario di 100/500 post: al riavvio legge tutto oggi.
        async for message in self.client.iter_messages(self.channel, limit=None, min_id=last_id):
            if message.date < start:
                break
            last_id = max(last_id, message.id)
            seen.update(asins_in_message(message))
        # Commit solo a lettura completata: un errore non lascia uno storico parziale.
        self.day, self.seen, self.last_id = day, seen, last_id

    async def send_once(self, asin, send, reserve):
        """Serializza controllo e invio; gli esiti ambigui restano riservati."""
        asin = asin.upper()
        async with self.lock:
            await self.refresh()  # Se fallisce, non viene inviato nulla.
            if asin in self.seen:
                return None
            # Prima dell'invio: nessun secondo tentativo in caso di timeout/DB KO.
            self.seen.add(asin)
            await asyncio.to_thread(reserve, asin)
            return await send()
