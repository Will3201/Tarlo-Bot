"""Read a bounded, fixed public Telegram channel history; never publish."""
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

BASE = 'https://t.me/s/TarloDelRisparmio'

class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()
    def text(self):
        if self.tag == 'br':
            return '\n'
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)

class Document(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, text):
        self.stack[-1].children.append(text)

def parse_page(raw):
    root = Document(raw.decode('utf-8')).root
    posts = []
    for node in root.walk():
        ident = node.attrs.get('data-post','')
        if not re.fullmatch(r'TarloDelRisparmio/\d+', ident):
            continue
        descendants = list(node.walk())
        dates = [x.attrs['datetime'] for x in descendants if x.tag == 'time' and 'datetime' in x.attrs]
        if not dates:
            continue
        texts = [x for x in descendants if 'tgme_widget_message_text' in x.attrs.get('class','').split()]
        links = [x.attrs['href'] for t in texts for x in t.walk() if x.tag == 'a' and 'href' in x.attrs]
        photos = []
        for x in descendants:
            if 'tgme_widget_message_photo_wrap' in x.attrs.get('class','').split():
                m = re.search(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)", x.attrs.get('style',''))
                if m:
                    photos.append(m.group(1))
        posts.append({'id':ident,'url':'https://t.me/'+ident,'date':dates[0],
                      'text':'\n'.join(t.text() for t in texts),'links':links,'photos':photos})
    return posts

def collect(request, folder):
    day = datetime.strptime(request['day'], '%Y-%m-%d').date()
    today = datetime.now(ZoneInfo('Europe/Rome')).date()
    if not today-timedelta(days=7) <= day < today:
        raise ValueError('Only a completed day within the last week is allowed')
    start = datetime.combine(day, datetime.min.time(), ZoneInfo('Europe/Rome'))
    end = start+timedelta(days=1)
    deadline = time.monotonic()+300
    before = None
    posts, pages = {}, []
    covered = False
    error = None
    for _ in range(40):
        if time.monotonic() >= deadline:
            error = 'history_deadline'
            break
        url = BASE + (f'?before={before}' if before else '')
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Cache-Control':'no-cache'})
            with urllib.request.urlopen(req, timeout=min(25,max(1,int(deadline-time.monotonic())))) as response:
                if not response.url.startswith(BASE):
                    raise ValueError('Unexpected redirect')
                raw = response.read(4_000_001)
            if len(raw)>4_000_000:
                raise ValueError('Page too large')
            found = parse_page(raw)
            if not found:
                raise ValueError('No timestamped posts; coverage unknown')
            dates = [datetime.fromisoformat(p['date'].replace('Z','+00:00')) for p in found]
            if any(d.tzinfo is None for d in dates):
                raise ValueError('Missing timezone')
            pages.append({'url':url,'oldest':min(dates).isoformat(),'newest':max(dates).isoformat(),'count':len(found)})
            for p,d in zip(found,dates):
                if start <= d < end:
                    posts[p['id']] = p
            if min(dates)<start:
                covered = True
                break
            next_before = min(int(p['id'].split('/')[1]) for p in found)
            if before is not None and next_before>=before:
                raise ValueError('Pagination made no progress')
            before = next_before
        except Exception as exc:
            error = type(exc).__name__+': '+str(exc)[:200]
            break
    result = {'day':day.isoformat(),'window_start':start.isoformat(),'window_end':end.isoformat(),
              'status':'history_ready' if covered else 'history_incomplete','coverage_verified':covered,
              'pages':pages,'posts':sorted(posts.values(),key=lambda p:p['date']),
              'error':error,'publication':False,'captured_at':datetime.now(timezone.utc).isoformat()}
    (folder/'history.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return {'status':result['status'],'coverage_verified':covered,'post_count':len(posts),
            'history_path':str(folder/'history.json'),'reason':error,'publication':False}
