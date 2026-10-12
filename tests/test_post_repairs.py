import unittest,json,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from post_repairs import PostRepairs

class Client:
 def __init__(self,m): self.m=m
 async def iter_messages(self,*a,**kw):
  yield self.m
 async def get_messages(self,*a,**kw): return self.m

class RepairsTests(unittest.IsolatedAsyncioTestCase):
 async def run_case(self,marker=False,timeout=False):
  now=datetime.now(timezone.utc)
  text='🛒 Lotus Test\n15,44 €\n#IlTarloDelRisparmio'
  if marker: text+=' #TarloCorrezionetest42'
  m=SimpleNamespace(date=now-timedelta(minutes=10),id=42,photo=True,raw_text=text,entities=[SimpleNamespace(url='https://www.amazon.it/dp/B0051GCRA4')])
  b=SimpleNamespace(edit_message_media=AsyncMock(return_value=SimpleNamespace(message_id=42)))
  if timeout: b.edit_message_media.side_effect=TimeoutError()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'request.json'
   p.write_text(json.dumps({'id':'test','start':(now-timedelta(hours=1)).isoformat(),'end':(now-timedelta(minutes=1)).isoformat(),'expires':(now+timedelta(hours=1)).isoformat(),'snapshots':{'B0051GCRA4':{'titolo':'Lotus Test','prezzo_attuale':'15,44','prezzo_precedente':'19,99','source_label':'12/10 alle 18:48','coupon':None}}}))
   r=PostRepairs(Client(m),b,'@TarloDelRisparmio',lambda a:None,lambda p,**kw:b'png','tarlodelris06-21',p)
   await r.run()
   return r,b
 async def test_snapshot_corrects_existing_message_and_labels_source(self):
  r,b=await self.run_case()
  self.assertEqual(r.results[-1]['status'],'corrected')
  b.edit_message_media.assert_awaited_once()
  args=b.edit_message_media.call_args.kwargs
  self.assertEqual(args['message_id'],42)
  self.assertIn('(-23%)',args['media'].caption)
  self.assertIn('screenshot Amazon',args['media'].caption)
 async def test_already_corrected_marker_never_edits_again(self):
  r,b=await self.run_case(marker=True)
  b.edit_message_media.assert_not_awaited()
 async def test_uncertain_edit_is_not_retried(self):
  r,b=await self.run_case(timeout=True)
  b.edit_message_media.assert_awaited_once()
  self.assertEqual(r.results[-1]['status'],'uncertain')
