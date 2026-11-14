"""Region-aware posting windows (labeled estimates) and the publish-kit template.

There is no reliable public data for exact best posting times; these are common starting
windows in the audience's local time, converted to the creator's time zone. The creator's own
analytics always win, so every result says where to find them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

REGIONS = {
    "us": "America/New_York", "usa": "America/New_York", "united states": "America/New_York",
    "us east": "America/New_York", "us west": "America/Los_Angeles", "canada": "America/Toronto",
    "uk": "Europe/London", "united kingdom": "Europe/London", "ireland": "Europe/Dublin",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "europe": "Europe/Berlin", "turkey": "Europe/Istanbul", "russia": "Europe/Moscow",
    "india": "Asia/Kolkata", "bangladesh": "Asia/Dhaka", "pakistan": "Asia/Karachi", "nepal": "Asia/Kathmandu",
    "sri lanka": "Asia/Colombo", "uae": "Asia/Dubai", "saudi arabia": "Asia/Riyadh", "middle east": "Asia/Dubai",
    "egypt": "Africa/Cairo", "nigeria": "Africa/Lagos", "south africa": "Africa/Johannesburg",
    "indonesia": "Asia/Jakarta", "malaysia": "Asia/Kuala_Lumpur", "singapore": "Asia/Singapore",
    "philippines": "Asia/Manila", "vietnam": "Asia/Ho_Chi_Minh", "thailand": "Asia/Bangkok",
    "japan": "Asia/Tokyo", "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "china": "Asia/Shanghai",
    "australia": "Australia/Sydney", "new zealand": "Pacific/Auckland",
    "brazil": "America/Sao_Paulo", "mexico": "America/Mexico_City", "argentina": "America/Argentina/Buenos_Aires",
    "latam": "America/Mexico_City",
}

# Weekend days (Mon=0) where they differ from Sat/Sun.
WEEKENDS = {"bangladesh": (4, 5), "saudi arabia": (4, 5), "egypt": (4, 5), "nepal": (5,), "middle east": (4, 5)}

# (label, start_hour, end_hour) in the AUDIENCE's local time - generic short-form starting points.
WINDOWS = {
    "weekday": [("lunch break", 12, 14), ("evening (strongest for gaming)", 18, 22)],
    "weekend": [("late morning", 10, 13), ("evening", 19, 23)],
}

ANALYTICS = {
    "youtube": "YouTube Studio > Analytics > Audience > 'When your viewers are on YouTube'",
    "tiktok": "TikTok Studio > Analytics > Followers > 'Most active times' (needs a few posts first)",
    "instagram": "Instagram Professional dashboard > Total followers > 'Most active times'",
    "facebook": "Meta Business Suite > Insights > Audience",
}


def local_zone() -> ZoneInfo | None:
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    return ZoneInfo(key) if key else None


def posting_windows(region: str, creator_tz: str | None = None, days: int = 7) -> dict:
    zone = REGIONS.get(region.strip().lower()) if region else None
    if not zone:
        return {"region": region, "error": f"unknown region; use one of: {', '.join(sorted(set(REGIONS)))}",
                "analytics": ANALYTICS}
    aud = ZoneInfo(zone)
    me = ZoneInfo(creator_tz) if creator_tz else (local_zone() or aud)
    today = datetime.now(aud).date()
    plan = []
    for d in range(days):
        day = today + timedelta(days=d)
        kind = "weekend" if day.weekday() in WEEKENDS.get(region.strip().lower(), (5, 6)) else "weekday"
        for label, a, b in WINDOWS[kind]:
            start = datetime(day.year, day.month, day.day, a, tzinfo=aud)
            end = datetime(day.year, day.month, day.day, b % 24, tzinfo=aud) + timedelta(days=1 if b >= 24 else 0)
            plan.append({
                "day": day.strftime("%a %d %b"), "window": label,
                "audience_time": f"{start:%H:%M}-{end:%H:%M} {start.tzname()}",
                "your_time": f"{start.astimezone(me):%a %H:%M}-{end.astimezone(me):%H:%M} {start.astimezone(me).tzname()}",
                "post_at_your_time": (start - timedelta(minutes=30)).astimezone(me).strftime("%a %H:%M"),
            })
    return {"region": region, "audience_zone": zone, "your_zone": str(me), "windows": plan,
            "confidence": "estimate - generic short-form patterns, not platform data",
            "tip": "Post ~30 min before the window so the first-hour push lands inside it. "
                   "After 5-10 posts, switch to your own analytics.",
            "analytics": ANALYTICS}


KIT = """# Publish kit: {title}

Video: `{video}`
Length: {length:.1f}s · {orientation} · region: {region} · language: {language}

## Titles (pick one per platform)
{titles}

## Description / caption
{description}

## Hashtags
{hashtags}

## Posting plan ({confidence})
{windows}

Where your real numbers are:
{analytics}

## Research notes
{notes}
"""


def _tags(hashtags) -> str:
    """One line of tags, or one line per platform when given {platform: [tags]}."""
    if isinstance(hashtags, dict):
        return "\n".join(f"- {p}: {' '.join(t)}" for p, t in hashtags.items() if t)
    return " ".join(hashtags or [])


def write_kit(path: str, edit: dict, video: str, region: str, titles: list[str], description: str,
              hashtags, notes: str = "", creator_tz: str | None = None) -> str:
    from .edit import total
    pw = posting_windows(region, creator_tz, 7) if region else {"windows": [], "confidence": "no region given",
                                                                "analytics": ANALYTICS}
    windows = "\n".join(f"- {w['day']} · {w['window']}: audience {w['audience_time']} → "
                        f"**post {w['post_at_your_time']}** your time" for w in pw.get("windows", [])[:6])
    text = KIT.format(
        title=edit.get("title", ""), video=video, length=total(edit), orientation=edit["orientation"],
        region=region or "global", language=edit.get("language", "en"),
        titles="\n".join(f"- {t}" for t in titles) or "- (add titles)",
        description=description or "(add description)", hashtags=_tags(hashtags) or "(add hashtags)",
        confidence=pw.get("confidence", ""), windows=windows or "- (no region given)",
        analytics="\n".join(f"- {k}: {v}" for k, v in pw["analytics"].items()), notes=notes or "-")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
