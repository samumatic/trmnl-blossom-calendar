"""Blossom Calendar: reads one or more iCal (ICS) links and returns the coming days as JSON for the views.

TRMNL's polling reads text/calendar as JSON and drops it, so the transform fetches the links itself.
Only the standard library and requests (both on TRMNL's serverless Python). The parser covers what
Google, Apple, Outlook, Nextcloud and Proton publish: folded lines, TZID/UTC/floating times, all-day
events, DURATION, RRULE (FREQ, INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY, BYMONTH), EXDATE, moved
occurrences (RECURRENCE-ID) and cancelled events.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import requests

MAX_URLS = 3
MAX_EVENTS = 60
MAX_PER_DAY = 12
FETCH_TIMEOUT = 3.5
MAX_STEPS = 4000
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
# Outlook writes Windows zone names; the common ones in Europe and the US.
WINDOWS_ZONES = {
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "Central European Standard Time": "Europe/Warsaw",
    "GMT Standard Time": "Europe/London", "E. Europe Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Helsinki", "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles", "UTC": "UTC",
}


# ---------- time zones ----------

def zone(name, fallback):
    if not name:
        return fallback
    name = name.strip().strip('"')
    name = WINDOWS_ZONES.get(name, name)
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
        # "/mozilla.org/20050126_1/Europe/Berlin" and similar prefixes
        m = re.search(r"([A-Za-z]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)$", name)
        if m:
            try:
                return ZoneInfo(m.group(1))
            except Exception:
                pass
    return fallback


def user_zone(trmnl):
    user = trmnl.get("user") or {}
    name = user.get("time_zone_iana")
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    offset = user.get("utc_offset")
    if isinstance(offset, (int, float)):
        return timezone(timedelta(seconds=offset))
    return timezone.utc


# ---------- ICS parsing ----------

def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text).split("\n")


def parse_line(line):
    """'DTSTART;TZID=Europe/Berlin:20261109T090000' -> ('DTSTART', {'TZID': ...}, '2026...')."""
    head, sep, value = "", "", ""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            sep = ":"
            break
    if not sep:
        return None
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def unescape(text):
    return (text.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\").strip())


def parse_when(value, params, tz):
    """A DATE or DATE-TIME value: (datetime or date, is_all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        return None, False
    y, mo, d, h, mi, s, z = m.groups()
    naive = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    if z:
        return naive.replace(tzinfo=timezone.utc), False
    return naive.replace(tzinfo=zone(params.get("TZID"), tz)), False


def parse_duration(value):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return None
    sign, w, d, h, mi, s = m.groups()
    delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0),
                      seconds=int(s or 0))
    return -delta if sign == "-" else delta


def parse_events(text, tz):
    events, current = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            current = {"exdates": set()}
            continue
        if current is None:
            continue
        if line == "END:VEVENT":
            events.append(current)
            current = None
            continue
        parsed = parse_line(line)
        if not parsed:
            continue
        name, params, value = parsed
        if name == "SUMMARY":
            current["title"] = unescape(value)
        elif name == "UID":
            current["uid"] = value.strip()
        elif name == "STATUS":
            current["status"] = value.strip().upper()
        elif name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
            when, all_day = parse_when(value, params, tz)
            if when is not None:
                current[name] = when
                if name == "DTSTART":
                    current["all_day"] = all_day
        elif name == "DURATION":
            current["duration"] = parse_duration(value)
        elif name == "RRULE":
            current["rrule"] = dict(p.partition("=")[::2] for p in value.strip().split(";") if "=" in p)
        elif name == "EXDATE":
            for v in value.split(","):
                when, _ = parse_when(v, params, tz)
                if when is not None:
                    current["exdates"].add(key_of(when))
    return events


def key_of(when):
    """Compares occurrences across zones: UTC instant for times, the date for all-day."""
    if isinstance(when, datetime):
        return when.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return when


# ---------- recurrence ----------

def add_months(d, n):
    m = d.month - 1 + n
    return d.year + m // 12, m % 12 + 1


def days_in_month(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def nth_weekdays(y, m, rule_days):
    """BYDAY in a month: '2MO', '-1FR', 'TU' -> the matching dates."""
    out = []
    last = days_in_month(y, m)
    for spec in rule_days:
        mm = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", spec)
        if not mm or mm.group(2) not in WEEKDAYS:
            continue
        wd = WEEKDAYS.index(mm.group(2))
        days = [d for d in range(1, last + 1) if date(y, m, d).weekday() == wd]
        n = mm.group(1)
        if n is None:
            out.extend(days)
        else:
            n = int(n)
            idx = n - 1 if n > 0 else n
            if -len(days) <= idx < len(days):
                out.append(days[idx])
    return sorted(set(out))


def occurrences(start, rule, until_window):
    """Start times of a recurring event, from its first one to the end of the window (inclusive)."""
    freq = rule.get("FREQ", "").upper()
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        u, _ = parse_when(rule["UNTIL"], {}, start.tzinfo if isinstance(start, datetime) else timezone.utc)
        until = u
    by_day = [d.strip().upper() for d in rule.get("BYDAY", "").split(",") if d.strip()]
    by_month_day = [int(d) for d in rule.get("BYMONTHDAY", "").split(",") if re.fullmatch(r"-?\d+", d.strip())]
    by_month = [int(m) for m in rule.get("BYMONTH", "").split(",") if m.strip().isdigit()]
    is_dt = isinstance(start, datetime)
    start_day = start.date() if is_dt else start
    clock = start.timetz() if is_dt else None

    def at(d):
        if not is_dt:
            return d
        return datetime.combine(d, time(clock.hour, clock.minute, clock.second), tzinfo=start.tzinfo)

    def past_until(when):
        if until is None:
            return False
        if isinstance(until, datetime) and is_dt:
            return when > until
        u = until.date() if isinstance(until, datetime) else until
        w = when.date() if is_dt else when
        return w > u

    def candidates():
        step = 0
        while step < MAX_STEPS:
            if freq == "DAILY":
                d = start_day + timedelta(days=step * interval)
                if (not by_month or d.month in by_month) and (not by_day or WEEKDAYS[d.weekday()] in
                                                                [b[-2:] for b in by_day]):
                    yield [d]
                else:
                    yield []
            elif freq == "WEEKLY":
                week = start_day - timedelta(days=start_day.weekday()) + timedelta(weeks=step * interval)
                wds = [WEEKDAYS.index(b[-2:]) for b in by_day if b[-2:] in WEEKDAYS] or [start_day.weekday()]
                yield [week + timedelta(days=w) for w in sorted(set(wds))]
            elif freq == "MONTHLY":
                y, m = add_months(start_day, step * interval)
                if by_month and m not in by_month:
                    yield []
                elif by_day:
                    yield [date(y, m, d) for d in nth_weekdays(y, m, by_day)]
                else:
                    last = days_in_month(y, m)
                    mds = by_month_day or [start_day.day]
                    yield [date(y, m, d if d > 0 else last + d + 1) for d in mds if 1 <= (d if d > 0 else last + d + 1) <= last]
            elif freq == "YEARLY":
                y = start_day.year + step * interval
                months = by_month or [start_day.month]
                out = []
                for m in months:
                    if by_day:
                        out += [date(y, m, d) for d in nth_weekdays(y, m, by_day)]
                    else:
                        for d in (by_month_day or [start_day.day]):
                            last = days_in_month(y, m)
                            d = d if d > 0 else last + d + 1
                            if 1 <= d <= last:
                                out.append(date(y, m, d))
                yield sorted(out)
            else:
                return
            step += 1

    seen = 0
    for group in candidates():
        for d in group:
            if d < start_day:
                continue
            when = at(d)
            if past_until(when):
                return
            seen += 1
            if count is not None and seen > count:
                return
            if d > until_window:
                return
            yield when


# ---------- window ----------

def as_local(when, tz, end=False):
    """A start or end as local datetime; all-day dates become local midnight."""
    if isinstance(when, datetime):
        return when.astimezone(tz)
    return datetime.combine(when, time(0), tzinfo=tz)


def expand(events, tz, first_day, last_day):
    """Every occurrence overlapping [first_day, last_day], as (start, end, all_day, title)."""
    moved = {}
    for ev in events:
        if "RECURRENCE-ID" in ev and ev.get("uid"):
            moved[(ev["uid"], key_of(ev["RECURRENCE-ID"]))] = ev
    win_start = datetime.combine(first_day, time(0), tzinfo=tz)
    win_end = datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=tz)
    out = []

    def add(ev, start):
        if ev.get("status") == "CANCELLED":
            return
        all_day = ev.get("all_day", False)
        if "DTEND" in ev and "DTSTART" in ev:
            length = ev["DTEND"] - ev["DTSTART"]
        elif ev.get("duration") is not None:
            length = ev["duration"]
        else:
            length = timedelta(days=1) if all_day else timedelta(0)
        if all_day and isinstance(length, timedelta) and length < timedelta(days=1):
            length = timedelta(days=1)
        s = as_local(start, tz)
        e = as_local(start + length, tz) if all_day else (start + length).astimezone(tz)
        if e < win_start or s >= win_end or (e == win_start and length):
            return
        out.append((s, e, all_day, ev.get("title") or ""))

    for ev in events:
        if "DTSTART" not in ev or "RECURRENCE-ID" in ev:
            continue
        rule = ev.get("rrule")
        if not rule:
            add(ev, ev["DTSTART"])
            continue
        for when in occurrences(ev["DTSTART"], rule, last_day):
            k = key_of(when)
            if k in ev["exdates"]:
                continue
            override = moved.get((ev.get("uid"), k))
            add(override, override["DTSTART"]) if override and "DTSTART" in override else add(ev, when)
    for ev in moved.values():
        if "DTSTART" in ev:
            parent = any(e.get("uid") == ev.get("uid") and e.get("rrule") for e in events)
            if not parent:
                add(ev, ev["DTSTART"])
    return out


def clock(dt):
    h12 = dt.hour % 12 or 12
    return {"t24": f"{dt.hour:02d}:{dt.minute:02d}",
            "t12": f"{h12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"}


def day_info(d, today):
    return {"wd": d.weekday(), "day": d.day, "month": d.month, "year": d.year, "rel": (d - today).days,
            "iso": d.isoformat()}


def build(occ, tz, today, days_ahead):
    days = []
    total = 0
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        day_start = datetime.combine(d, time(0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        items = []
        for s, e, all_day, title in occ:
            if not (s < day_end and (e > day_start or (e == s and day_start <= s))):
                continue
            item = {"title": title or "–", "all_day": all_day}
            if not all_day:
                if s >= day_start:
                    item["start"] = clock(s)
                if e < day_end and e > s:
                    item["end"] = clock(e)
                elif e >= day_end and s >= day_start:
                    item["open"] = True
                if s < day_start and e >= day_end:
                    item["all_day"] = True
            if all_day or s < day_start:
                span = (e.date() - s.date()).days if all_day else None
                if span and span > 1:
                    item["part"] = [(d - s.date()).days + 1, span]
            items.append((0 if item["all_day"] else 1, s, title, item))
        items.sort(key=lambda t: (t[0], t[1], t[2]))
        items = [t[3] for t in items][:MAX_PER_DAY]
        if total + len(items) > MAX_EVENTS:
            items = items[:max(0, MAX_EVENTS - total)]
        total += len(items)
        if items or i == 0:
            days.append(dict(day_info(d, today), events=items))
    return days


def month_grid(today, occ, tz):
    """The current month, Monday first: day numbers, today, and days with something on."""
    first = today.replace(day=1)
    last = days_in_month(today.year, today.month)
    busy = set()
    for s, e, all_day, _ in occ:
        d = s.date()
        stop = (e - timedelta(seconds=1)).date() if e > s else d
        while d <= stop and (d.year, d.month) <= (today.year, today.month):
            if d.month == today.month and d.year == today.year:
                busy.add(d.day)
            d += timedelta(days=1)
    cells = [None] * first.weekday() + list(range(1, last + 1))
    cells += [None] * (-len(cells) % 7)
    weeks = [[{"d": c, "today": c == today.day, "busy": c in busy} if c else {"d": None}
              for c in cells[i:i + 7]] for i in range(0, len(cells), 7)]
    return weeks


# ---------- input ----------

def links(fields):
    raw = str(fields.get("ics_urls") or "")
    urls = []
    for part in re.split(r"[\s,]+", raw):
        part = part.strip()
        if not part:
            continue
        if part.lower().startswith("webcal://"):
            part = "https://" + part[9:]
        if re.match(r"https?://", part, re.I):
            urls.append(part)
    return urls[:MAX_URLS]


def fetch(url):
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": "TRMNL Blossom Calendar"})
        if r.status_code == 200 and "BEGIN:VCALENDAR" in r.text[:2000]:
            return r.text
    except Exception:
        pass
    return None


def demo_events(today, tz, lang):
    """Made-up events relative to today for the preview: no real person's data."""
    words = {
        "de": ["Yoga mit Mia", "Brunch bei Oma", "Maniküre", "Mädelsabend", "Geburtstag von Lena",
               "Blumen gießen", "Kino: Liebeskomödie", "Wochenmarkt", "Picknick im Park", "Tanzkurs"],
        "en": ["Yoga with Mia", "Brunch at Grandma's", "Manicure", "Girls' night", "Lena's birthday",
               "Water the flowers", "Cinema: rom-com", "Farmers market", "Picnic in the park", "Dance class"],
    }[lang]

    def at(day, h, m=0):
        return datetime.combine(today + timedelta(days=day), time(h, m), tzinfo=tz)

    plan = [(0, 8, 0, 60, 0), (0, 11, 30, 90, 1), (0, 19, 0, 0, 5), (1, 16, 0, 60, 2), (1, 20, 0, 180, 3),
            (2, None, None, None, 4), (3, 18, 30, 120, 6), (4, 9, 0, 120, 7), (5, 13, 0, 180, 8),
            (6, 19, 0, 90, 9)]
    occ = []
    for day, h, m, mins, i in plan:
        if h is None:
            s = datetime.combine(today + timedelta(days=day), time(0), tzinfo=tz)
            occ.append((s, s + timedelta(days=1), True, words[i]))
        else:
            s = at(day, h, m)
            occ.append((s, s + timedelta(minutes=mins), False, words[i]))
    return occ


def language(trmnl, fields):
    lang = str(fields.get("language") or "auto").lower()
    if lang in ("de", "en"):
        return lang
    locale = str((trmnl.get("user") or {}).get("locale") or "en")[:2].lower()
    return "de" if locale == "de" else "en"


def run(input):
    trmnl = input.get("trmnl") or {}
    fields = (trmnl.get("plugin_settings") or {}).get("custom_fields_values") or {}
    tz = user_zone(trmnl)
    now = datetime.now(timezone.utc).astimezone(tz)
    today = now.date()
    try:
        days_ahead = max(1, min(14, int(str(fields.get("days_ahead") or "7"))))
    except ValueError:
        days_ahead = 7
    last_day = max(today + timedelta(days=days_ahead - 1),
                   today.replace(day=days_in_month(today.year, today.month)))
    first_day = today.replace(day=1)

    urls = links(fields)
    if not urls:
        occ = demo_events(today, tz, language(trmnl, fields))
        demo, failed = True, False
    else:
        demo = False
        # The polled body is used when TRMNL passed the calendar through as text; otherwise fetch it here.
        polled = input.get("data") if isinstance(input.get("data"), str) else None
        texts = []
        todo = list(urls)
        if polled and "BEGIN:VCALENDAR" in polled[:2000]:
            texts.append(polled)
            todo = todo[1:]
        if todo:
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                texts += list(pool.map(fetch, todo))
        good = [t for t in texts if t]
        failed = len(good) < len(urls)
        if not good:
            return {"cal": {"ok": False, "error": "fetch"}}
        events = []
        for t in good:
            try:
                events += parse_events(t, tz)
            except Exception:
                failed = True
        occ = expand(events, tz, first_day, last_day)

    # Today's agenda starts now: what already ended drops off; all-day events stay until midnight.
    upcoming = [o for o in occ if o[1] > now or (o[2] and o[1] > datetime.combine(today, time(0), tzinfo=tz))]
    return {"cal": {
        "ok": True,
        "demo": demo,
        "partial": failed,
        "today": day_info(today, today),
        "now": clock(now),
        "days": build(upcoming, tz, today, days_ahead),
        "month": month_grid(today, occ, tz),
    }}
