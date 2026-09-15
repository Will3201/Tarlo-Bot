"""Prepare public TikTok assets on the publishing branch; never publish posts."""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ROME = ZoneInfo("Europe/Rome")
BASE = "https://tarlo-bot-1.onrender.com"
MAX_BYTES = 8_000_000

class Stop(Exception):
    pass

def now():
    return datetime.now(timezone.utc)

def dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise Stop("Timestamp without timezone")
    return parsed

def slot_at(value):
    local = value.astimezone(ROME)
    if local.hour < 1:
        local -= timedelta(days=1)
        hour = 19
    else:
        hour = 1 + ((local.hour - 1) // 6) * 6
    return f"{local:%Y-%m-%d}-{hour:02d}"

def local_asset(path):
    if not isinstance(path, str) or not path.startswith(("media/", "prepared/")):
        raise Stop("Input must be an archived public asset")
    result = (ROOT / path).resolve()
    if ROOT not in result.parents or result.is_symlink() or not result.is_file():
        raise Stop("Invalid asset path")
    if result.stat().st_size > MAX_BYTES:
        raise Stop("Asset too large")
    return result

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

HTTP = urllib.request.build_opener(NoRedirect)

def fetch(path, method="GET", timeout=30):
    if not path.startswith("/daily/") or "?" in path or "#" in path:
        raise Stop("Only fixed daily endpoint paths are allowed")
    headers = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
    request = urllib.request.Request(BASE + path, data=b"" if method == "POST" else None,
                                     method=method, headers=headers)
    try:
        with HTTP.open(request, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise Stop("Response too large")
            return response.status, data
    except urllib.error.HTTPError as error:
        return error.code, error.read(4096)

def get_json(path, method="GET", timeout=30):
    code, raw = fetch(path, method, timeout)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        data = {"status": "non_json_response"}
    if not isinstance(data, dict):
        raise Stop("Expected JSON object")
    return code, data

def check_feed(feed, slot, current, excluded):
    if feed.get("status") != "ready" or feed.get("slot") != slot:
        raise Stop("Feed is not ready for the requested slot")
    if feed.get("day") != current.astimezone(ROME).date().isoformat():
        raise Stop("Incorrect Italian date")
    if feed.get("timezone") != "Europe/Rome":
        raise Stop("Unexpected feed timezone")
    start, end = dt(feed["window_start"]), dt(feed["window_end"])
    if abs((end - start).total_seconds() - 21600) > 1:
        raise Stop("Selection window is not six hours")
    if not current - timedelta(minutes=20) <= end <= current + timedelta(seconds=60):
        raise Stop("Selection window is not current")
    if dt(feed["valid_until"]) <= current + timedelta(minutes=5):
        raise Stop("Offer expires too soon")
    product = feed.get("product", {})
    asin = product.get("asin", "")
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        raise Stop("Invalid ASIN")
    if asin in excluded:
        raise Stop("ASIN already published, scheduled, or unresolved")
    if not start <= dt(product["pubblicato_il"]) <= end:
        raise Stop("Product was not reported within the selection window")
    if not re.fullmatch(r"/daily/media/" + re.escape(slot) + r"/[a-f0-9]+\.png",
                        feed.get("media_path", "")):
        raise Stop("Unexpected source media path")
    if not re.fullmatch(r"[a-f0-9]{64}", feed.get("sha256", "")):
        raise Stop("Missing source SHA256")
    if feed.get("live_verified") is False:
        if feed.get("price_source") != "telegram_snapshot" or not feed.get("price_observed_at"):
            raise Stop("Unverified price lacks snapshot provenance")
        dt(feed["price_observed_at"])
    return product

def excluded_from_records(current, slot):
    excluded = set()
    for path in (ROOT / "records").glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("slot") == slot and record.get("status") in ("attempted", "scheduled", "published"):
            raise Stop("Slot record already exists; reconcile before preparing")
        if record.get("status") not in ("attempted", "scheduled", "published"):
            continue
        timestamps = []
        for key in ("published_at", "scheduled_at", "attempted_at"):
            if record.get(key):
                timestamps.append(dt(record[key]))
        publication = record.get("publicationDate", {})
        if publication.get("dateTime"):
            value = datetime.fromisoformat(publication["dateTime"])
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo(publication["timezone"]))
            timestamps.append(value)
        uncertain = record.get("status") == "attempted"
        recent = any(t >= current - timedelta(hours=24) for t in timestamps)
        if uncertain or recent or not timestamps:
            values = record.get("asins", []) + [record.get("asin")]
            excluded.update(x for x in values if isinstance(x, str))
    return excluded

def image_to_jpeg(raw, destination, expected_sha=None, require_png=False):
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and sha != expected_sha:
        raise Stop("SHA256 mismatch")
    if len(raw) > MAX_BYTES:
        raise Stop("Image too large")
    with Image.open(BytesIO(raw)) as im:
        if require_png and im.format != "PNG":
            raise Stop("Source is not PNG")
        if im.format not in ("PNG", "JPEG") or getattr(im, "n_frames", 1) != 1:
            raise Stop("Unsupported or animated image")
        if not (1 <= im.width <= 4096 and 1 <= im.height <= 4096):
            raise Stop("Invalid dimensions")
        im.verify()
    with Image.open(BytesIO(raw)) as original:
        original.load()
        im = ImageOps.exif_transpose(original).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        rgb = bg.convert("RGB")
        rgb.thumbnail((1080, 1080), Image.Resampling.LANCZOS)
        rgb.save(destination, "JPEG", quality=95, subsampling=0, optimize=True)
    jpg = destination.read_bytes()
    with Image.open(destination) as check:
        check.load()
        if check.format != "JPEG" or check.mode != "RGB" or max(check.size) > 1080:
            raise Stop("Output JPEG verification failed")
        size = list(check.size)
    return {"source_sha256": sha, "jpeg_sha256": hashlib.sha256(jpg).hexdigest(),
            "jpeg_dimensions": size, "jpeg_mode": "RGB", "jpeg_bytes": len(jpg),
            "media_path": destination.relative_to(ROOT).as_posix()}

def prepare_single(request, folder):
    current = now()
    slot = request["slot"]
    if slot != slot_at(current):
        raise Stop("Requested slot is not current; no previous-slot recovery")
    excluded = excluded_from_records(current, slot) | set(request.get("excluded_asins", []))
    deadline = time.monotonic() + 360
    post_outcome = "unknown"
    try:
        code, initial = get_json("/daily/prepare", "POST", timeout=45)
        post_outcome = {"http_status": code, "status": initial.get("status")}
        if code == 429:
            return {"status": "rate_limited", "retry_after_seconds": initial.get("retry_after_seconds", 900),
                    "post": post_outcome}
        if code not in (200, 202, 502, 503, 504):
            return {"status": "prepare_rejected", "post": post_outcome}
    except (TimeoutError, OSError, urllib.error.URLError):
        # A timed-out POST may still have executed. Poll; never resend blindly.
        post_outcome = "request_outcome_uncertain_polling_only"
    last = {}
    while time.monotonic() < deadline:
        try:
            remaining = max(1, min(25, int(deadline - time.monotonic())))
            code, state = get_json("/daily/status", timeout=remaining)
            code_latest, feed = get_json("/daily/latest", timeout=remaining)
            last = {"http_status": code, "state": state, "latest_http_status": code_latest,
                    "latest_status": feed.get("status")}
            if code_latest == 200 and feed.get("status") == "ready":
                check_feed(feed, slot, now(), excluded)
                code_image, raw = fetch(feed["media_path"])
                if code_image != 200:
                    raise Stop("Source PNG could not be downloaded")
                stats = image_to_jpeg(raw, folder / "offer.jpg", feed["sha256"], require_png=True)
                (folder / "source.png").write_bytes(raw)
                return {"status": "ready", "feed": feed, **stats,
                        "original_media_path": (folder / "source.png").relative_to(ROOT).as_posix(),
                        "post": post_outcome, "visual_review_required": True,
                        "publication": False}
            if code_latest == 200 and feed.get("status") == "expired":
                return {"status": "expired", "post": post_outcome}
            if code == 200 and state.get("status") in ("no_recent_offer", "no_verified_offer", "failed"):
                return {"status": state["status"], "post": post_outcome, "last": last}
        except (TimeoutError, OSError, urllib.error.URLError):
            last = {"status": "temporary_network_error"}
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    return {"status": "prepare_timeout", "post": post_outcome, "last": last}

def self_test(request, folder):
    raw = local_asset("media/2026-09-14-13.jpg").read_bytes()
    stats = image_to_jpeg(raw, folder / "conversion-test.jpg")
    refused = False
    try:
        image_to_jpeg(raw, folder / "must-not-exist.jpg", "0" * 64)
    except Stop:
        refused = True
    assert refused, "Mismatch must be rejected"
    assert not (folder / "must-not-exist.jpg").exists()
    assert slot_at(datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc)) == "2026-09-14-19"
    assert slot_at(datetime(2026, 9, 14, 23, 0, tzinfo=timezone.utc)) == "2026-09-15-01"
    return {"status": "self_test_passed", "checks": ["JPEG_RGB_max1080", "hash_mismatch_rejected",
             "Italian_midnight_slot", "Italian_01_slot"], **stats, "publication": False}

def main():
    request = json.loads((ROOT / "requests/current.json").read_text())
    ident = request.get("id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,70}", ident):
        raise Stop("Invalid request ID")
    folder = ROOT / "prepared" / ident
    if (folder / "result.json").exists():
        print("Request already processed; no repeat.")
        return
    folder.mkdir(parents=True, exist_ok=True)
    result = {"request_id": ident, "kind": request.get("kind"), "started_at": now().isoformat(),
              "publication": False}
    try:
        issued = dt(request["requested_at"])
        if not now() - timedelta(minutes=20) <= issued <= now() + timedelta(seconds=60):
            raise Stop("Request is stale or from the future")
        kind = request["kind"]
        if kind == "self_test":
            result.update(self_test(request, folder))
        elif kind == "single":
            result.update(prepare_single(request, folder))
        elif kind == "history":
            from channel_history import collect
            result.update(collect(request, folder))
        elif kind == "convert":
            source = local_asset(request["source_path"])
            if not re.fullmatch(r"[a-f0-9]{64}", request.get("source_sha256", "")):
                raise Stop("Conversion requires source SHA256")
            result.update(image_to_jpeg(source.read_bytes(), folder / "offer.jpg",
                                       request["source_sha256"]))
            result.update(status="ready", visual_review_required=True)
        else:
            raise Stop("Unknown fixed operation")
    except Exception as error:
        result.update(status="blocked", reason=f"{type(error).__name__}: {str(error)[:240]}")
    result["finished_at"] = now().isoformat()
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result.get(key) for key in ("request_id", "kind", "status", "reason")}))
    if not (folder / "result.json").is_file():
        raise Stop("Result was not saved")

if __name__ == "__main__":
    main()
