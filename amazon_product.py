"""Read only the requested Amazon product's own price blocks; fail closed."""
import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from bs4 import BeautifulSoup
from coupon_pricing import leggi_coupon_amazon
from tarlo_daily import estrai_metriche

PRICE_BLOCKS = ('corePriceDisplay_desktop_feature_div', 'corePrice_feature_div',
                'corePrice_desktop', 'apex_desktop', 'unifiedPrice_feature_div')
LABELS = {'consigliato': r'prezzo\s+consigliato|recommended\s+(?:retail\s+)?price',
          'mediano': r'prezzo\s+mediano|median\s+price',
          'piu_basso_30gg': r'prezzo\s+pi[ùu]\s+basso.{0,50}30',
          'precedente': r'prezzo\s+precedente|previous\s+price'}
UNIT = re.compile(r'/\s*(?:\d+[,.]?\d*\s*)?(?:ml|l|kg|g|gr|cl|pz|conteggio|count|mese|month)\b', re.I)


def normalizza_titolo(value):
    # Commas carry model, size, flavour and quantity: never discard them.
    return ' '.join(html.unescape(str(value or '')).replace('\u200b', '').split())


def money(value):
    text = str(value or '').strip().replace('\xa0', ' ')
    if not re.fullmatch(r'(?:€\s*)?\d[\d., ]*(?:\s*€)?', text):
        return None
    text = text.replace('€', '').replace(' ', '')
    if ',' in text:
        if not re.fullmatch(r'\d+(?:\.\d{3})*(?:,\d{1,2})?', text):
            return None
        text = text.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', text):
        text = text.replace('.', '')
    elif not re.fullmatch(r'\d+(?:\.\d{1,2})?', text):
        return None
    try:
        result = Decimal(text)
        return result if result.is_finite() and 0 < result < 10000000 else None
    except InvalidOperation:
        return None


def euros(value):
    return f'{value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP):.2f}'.replace('.', ',')


def visible(node):
    return not any(p.get('aria-hidden') == 'true' or p.has_attr('hidden') or
                   re.search(r'display\s*:\s*none', p.get('style', ''), re.I)
                   for p in [node, *node.parents] if hasattr(p, 'get'))


def struck(node):
    return any(set(p.get('class', [])) & {'a-text-price', 'a-text-strike', 'basisPrice'} or
               p.get('data-a-strike') == 'true'
               for p in [node, *list(node.parents)[:3]] if hasattr(p, 'get'))


def unit_or_conditional(node):
    if node.get('data-a-size') in ('mini', 'small'):
        return True
    for parent in [node, *list(node.parents)[:3]]:
        ident = parent.get('id', '') if hasattr(parent, 'get') else ''
        if re.search(r'unitPrice|pricePerUnit|installment|sns|subscribe|usedPrice|shipping', ident, re.I):
            return True
    parent = node.parent
    if parent and len(parent.select('.a-price')) <= 1:
        text = parent.get_text(' ', strip=True)
        if UNIT.search(text) or re.search(r'al mese|per month|acquisto periodico|subscribe|usato|used', text, re.I):
            return True
    for sibling in list(node.next_siblings)[:3]:
        if hasattr(sibling, 'select') and sibling.select('.a-price, .a-offscreen'):
            continue
        text = sibling.get_text(' ', strip=True) if hasattr(sibling, 'get_text') else str(sibling)
        if UNIT.search(text):
            return True
    return False


def price_value(node):
    off = node.select_one('.a-offscreen')
    if off is not None and money(off.get_text(' ', strip=True)):
        return money(off.get_text(' ', strip=True))
    whole, fraction = node.select_one('.a-price-whole'), node.select_one('.a-price-fraction')
    if whole is not None:
        integer = re.sub(r'[^\d.,]', '', whole.get_text()).rstrip('.,')
        cents = re.sub(r'\D', '', fraction.get_text()) if fraction else ''
        if cents and len(cents) != 2:
            return None
        return money(integer + (',' + cents if cents else ''))
    return money(node.get_text(' ', strip=True))


def reference(blocks, current):
    candidates = []
    for block in blocks:
        for node in block.select('.basisPrice, .a-text-price, .a-text-strike, #listPrice'):
            if not visible(node):
                continue
            value = price_value(node)
            if value is None or value <= current:
                continue
            kind = 'non_identificato'
            for context in [node, node.parent]:
                if context is None:
                    continue
                labels = [key for key, pattern in LABELS.items()
                          if re.search(pattern, context.get_text(' ', strip=True), re.I)]
                if len(labels) == 1:
                    kind = labels[0]
                    break
            candidates.append((value, kind))
    known = set(c for c in candidates if c[1] != 'non_identificato')
    # Multiple references are legitimate, but must never be mixed. Prefer one
    # explicitly labelled RRP, then median; discard conflicting values.
    for kind in ('consigliato', 'mediano', 'piu_basso_30gg', 'precedente'):
        values = {value for value, label in known if label == kind}
        if len(values) == 1:
            return values.pop(), kind
        if len(values) > 1:
            return None, 'non_identificato'
    values = {value for value, _ in candidates}
    return (values.pop(), 'non_identificato') if len(values) == 1 else (None, 'non_identificato')


def extract_product(page, asin):
    if not re.fullmatch(r'[A-Z0-9]{10}', asin):
        return None
    soup = BeautifulSoup(page, 'html.parser')
    if soup.select_one('form[action*="validateCaptcha"]') or re.search(
            r'Inserisci i caratteri|automated access', page, re.I):
        return None
    selected = soup.select_one('input#ASIN, input[name="ASIN"]')
    if selected is None or selected.get('value', '').upper() != asin:
        return None
    title_node = soup.select_one('#productTitle')
    title = normalizza_titolo(title_node.get_text(' ', strip=True)) if title_node else ''
    if not title or title.casefold() in ('prodotto amazon', 'amazon.it', 'amazon'):
        return None
    blocks = [soup.find(id=ident) for ident in PRICE_BLOCKS]
    blocks = [b for b in blocks if b is not None and visible(b)]
    current = None
    for selector in ('.a-price[data-a-size="xl"]', '.a-price[data-a-size="l"]',
                     '.apexPriceToPay', '.priceToPay', '#priceblock_dealprice', '#priceblock_ourprice'):
        for block in blocks:
            values = set()
            for node in block.select(selector):
                if visible(node) and not struck(node) and not unit_or_conditional(node):
                    value = price_value(node)
                    if value:
                        values.add(value)
            if len(values) > 1:
                return None  # Ambiguous main prices: do not choose the cheapest.
            if values:
                current = values.pop()
                break
        if current:
            break
    if current is None:
        return None  # Never scan the full page for unrelated product prices.
    previous, kind = reference(blocks, current)
    image = soup.select_one('#landingImage, #imgBlkFront')
    image_url = image.get('data-old-hires') or image.get('src', '') if image else ''
    if image and not image_url:
        try:
            images = json.loads(image.get('data-a-dynamic-image', '{}'))
            image_url = max(images, key=lambda url: images[url][0] * images[url][1]) if images else ''
        except (ValueError, TypeError, KeyError, IndexError):
            image_url = ''
    availability = soup.select_one('#availability')
    unavailable = bool(availability and re.search(r'non disponibile|unavailable|esaurito',
                                                availability.get_text(' ', strip=True), re.I))
    breadcrumbs = [normalizza_titolo(n.get_text(' ', strip=True))
                   for n in soup.select('#wayfinding-breadcrumbs_feature_div a')]
    return {'asin': asin, 'asin_verificato': True, 'titolo': title, 'titolo_completo': title,
            'prezzo_attuale': euros(current), 'prezzo_precedente': euros(previous) if previous else None,
            'sconto': int(((previous-current)/previous*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if previous else 0,
            'tipo_riferimento': kind, 'immagine_url': image_url, 'categorie_amazon': breadcrumbs,
            'disponibile': bool(soup.select_one('#add-to-cart-button, #buy-now-button')) and not unavailable,
            'coupon': leggi_coupon_amazon(soup), 'verificato_il': datetime.now(timezone.utc).isoformat(),
            **estrai_metriche(soup)}
