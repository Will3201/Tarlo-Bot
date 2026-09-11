import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,Mock
from daily_dedup import DailyDedup

class Client:
    def __init__(self,messages): self.messages=messages
    async def iter_messages(self,channel,limit,min_id):
        for m in self.messages:
            if m.id>min_id: yield m

def msg(i,asin,at):
    return NS(id=i,date=at,raw_text='',entities=[NS(url='https://www.amazon.it/dp/'+asin+'?tag=test')])

class Tests(IsolatedAsyncioTestCase):
    async def test_restart_with_hidden_links(self):
        now=datetime(2026,9,11,14,tzinfo=timezone.utc)
        guard=DailyDedup(Client([msg(1,'B012345678',now)]),'test',lambda:now)
        send=AsyncMock()
        self.assertIsNone(await guard.send_once('B012345678',send,Mock()))
        send.assert_not_called()
    async def test_simultaneous_and_failure(self):
        guard=DailyDedup(Client([]),'test')
        send=AsyncMock(return_value='ok')
        await asyncio.gather(*(guard.send_once('B012345678',send,Mock()) for _ in range(5)))
        self.assertEqual(send.await_count,1)
        bad=AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError): await guard.send_once('B012345679',bad,Mock())
        self.assertIsNone(await guard.send_once('B012345679',bad,Mock()))
        self.assertEqual(bad.await_count,1)
    async def test_midnight_italy(self):
        now=[datetime(2026,9,11,21,59,tzinfo=timezone.utc)]
        guard=DailyDedup(Client([]),'test',lambda:now[0]); send=AsyncMock(return_value=1)
        await guard.send_once('B012345678',send,Mock())
        now[0]=datetime(2026,9,11,22,1,tzinfo=timezone.utc)
        await guard.send_once('B012345678',send,Mock())
        self.assertEqual(send.await_count,2)
    async def test_unreadable_history_blocks_send(self):
        class Broken(Client):
            async def iter_messages(self,*args,**kwargs):
                raise RuntimeError('history unavailable')
                yield
        guard=DailyDedup(Broken([]),'test'); send=AsyncMock()
        with self.assertRaises(RuntimeError): await guard.send_once('B012345678',send,Mock())
        send.assert_not_called()
    async def test_incremental_refresh(self):
        now=datetime.now(timezone.utc); client=Client([])
        guard=DailyDedup(client,'test',lambda:now)
        await guard.refresh()
        client.messages=[msg(2,'B012345678',now)]
        send=AsyncMock()
        self.assertIsNone(await guard.send_once('B012345678',send,Mock()))
        send.assert_not_called()
