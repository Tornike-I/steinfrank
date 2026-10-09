"""get_weather: daily forecast for ONE location (the runtime fans out several locations in parallel, each cached separately).

Provider: Open-Meteo (free, no API key). Forecast horizon: up to 16 days ahead.
inputs:  location (str), start_date (ISO date, default today), forecast_days (int 1-16), units (celsius|fahrenheit)
outputs: location, country, latitude, longitude, units, days[{date, condition, t_max, t_min, precipitation_mm, wind_max_kmh}], source
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"      # при установке можно заменить (тесты, свой прокси)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 16

SKILL = {
    "intents": ["weather forecast", "current weather", "compare temperatures"],
    "keywords": ["weather", "forecast", "temperature", "temperatures", "rain", "počasí", "předpověď", "погода", "погоду", "прогноз"],
    "examples": ["What's the weather in Prague tomorrow?", "Weather in Dubai and Tel Aviv", "Forecast for Berlin for 10 days",
                 "Compare temperatures in London, Paris and Berlin", "Погода в Киеве на завтра", "Počasí v Brně zítra"],
    "limits": {"forecast_days": "1-16 (provider horizon)", "units": "celsius or fahrenheit"},
    "freshness_seconds": 1800,
    "llm_slots": {},
}
CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog", 51: "light drizzle",
         53: "drizzle", 55: "dense drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
         75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent rain showers", 95: "thunderstorm",
         96: "thunderstorm with hail", 99: "thunderstorm with hail"}
# устаревшие/иноязычные названия и падежные формы крупных городов -> имя, которое знает геокодер
EXONYMS = {"kiev": "Kyiv", "киев": "Kyiv", "киеве": "Kyiv", "києві": "Kyiv", "київ": "Kyiv", "kyjev": "Kyiv", "kyjevě": "Kyiv",
           "прага": "Prague", "праге": "Prague", "praha": "Prague", "praze": "Prague", "москва": "Moscow", "москве": "Moscow",
           "варшава": "Warsaw", "варшаве": "Warsaw", "берлин": "Berlin", "берлине": "Berlin", "лондон": "London", "лондоне": "London",
           "париж": "Paris", "париже": "Paris", "вена": "Vienna", "вене": "Vienna", "vídeň": "Vienna", "vídni": "Vienna",
           "дубай": "Dubai", "дубае": "Dubai", "тель-авив": "Tel Aviv", "тель-авиве": "Tel Aviv", "brno": "Brno", "brně": "Brno",
           "минск": "Minsk", "минске": "Minsk", "одесса": "Odesa", "одессе": "Odesa", "odessa": "Odesa", "львов": "Lviv", "львове": "Lviv"}
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
       "eleven": 11, "twelve": 12, "fourteen": 14, "sixteen": 16}


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "frankenstein"}), timeout=12) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code == 429:
            raise ConnectionError(f"provider unavailable: HTTP {e.code}")
        raise ValueError(f"the weather provider rejected the request: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ConnectionError(f"provider unavailable: {e}")


def _days(daily):
    out = []
    for i, d in enumerate(daily["time"]):
        code = (daily.get("weather_code") or [None] * len(daily["time"]))[i]
        out.append({"date": d, "condition": CODES.get(code, "unknown") if code is not None else "unknown",
                    "t_max": daily["temperature_2m_max"][i], "t_min": daily["temperature_2m_min"][i],
                    "precipitation_mm": (daily.get("precipitation_sum") or [None] * len(daily["time"]))[i],
                    "wind_max_kmh": (daily.get("wind_speed_10m_max") or [None] * len(daily["time"]))[i]})
    return out


def run(inp):
    loc = inp.get("location")
    if not isinstance(loc, str) or not loc.strip():
        raise ValueError("location is required")
    days = int(inp.get("forecast_days", 1))
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"forecasts are available for 1-{MAX_DAYS} days only, not {days}")
    units = inp.get("units", "celsius")
    if units not in ("celsius", "fahrenheit"):
        raise ValueError("units must be celsius or fahrenheit")
    start = date.fromisoformat(inp.get("start_date") or date.today().isoformat())
    if (start - date.today()).days + days > MAX_DAYS:
        raise ValueError(f"the provider forecasts only {MAX_DAYS} days ahead")
    query = EXONYMS.get(loc.strip().lower(), loc.strip())
    geo = _get(GEO_URL, {"name": query, "count": 10, "language": "en", "format": "json"})
    if not geo.get("results"):
        raise ValueError(f"unknown location: {loc}")
    # одноимённых мест много («Kiev» — деревня в России, «Prague» — город в США): берём столицу/крупнейший город
    g = max(geo["results"], key=lambda r: (r.get("feature_code") in ("PPLC", "PPLA"), r.get("population") or 0))
    fc = _get(FORECAST_URL, {"latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto",
                             "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                             "start_date": start.isoformat(), "end_date": (start + timedelta(days=days - 1)).isoformat(),
                             "temperature_unit": units})
    return {"location": g["name"], "requested_location": loc, "country": g.get("country", ""), "latitude": g["latitude"],
            "longitude": g["longitude"], "units": units, "days": _days(fc["daily"]), "source": "open-meteo.com"}


FILLER = re.compile(r"^(?:uh+|um+|er+|eh|also|then|and|in|at|for|the|v|ve|в|во|и|a)\b[\s,]*", re.I)


def _cities(seg):
    """«Kiev and, uh, in Prague» -> [Kiev, Prague]: делим по запятым и союзам, убираем слова-паразиты и предлоги."""
    out = []
    for part in re.split(r",|;|&|\band\b|\bи\b|\ba\b|\bor\b", seg):
        p = part.strip(" '\"")
        while True:
            q = FILLER.sub("", p).strip(" '\"")
            if q == p:
                break
            p = q
        if p and p[0].isupper():
            out.append(p)
    return out


def parse_request(text, context):
    t = text.lower()
    if not any(k in t for k in SKILL["keywords"]):
        return None
    cities = []
    m = re.search(r"\b(?:in|for|at|v|ve|в|во)\s+(.+)", text)
    if m:
        seg = re.split(r"\b(?:tomorrow|today|tonight|right now|now|currently|for|this|next|on|during|zítra|dnes|teď|nyní|завтра|сегодня|сейчас|на)\b|[?.!]", m.group(1))[0]
        cities = _cities(seg)
    if not cities and context.get("previous"):
        prev = context["previous"] if isinstance(context["previous"], list) else [context["previous"]]
        cities = [p["location"] for p in prev if isinstance(p, dict) and p.get("location")]
    today = date.fromisoformat(context["today"])
    start, days = today, 1
    n = re.search(r"(\d+|" + "|".join(NUM) + r")\s*(?:-\s*)?(?:days?|dní|dny|дн\w*)", t)
    if n:
        days = int(n.group(1)) if n.group(1).isdigit() else NUM[n.group(1)]
    elif re.search(r"\b(?:week|týden|недел\w*)\b", t):
        days = 7
    if re.search(r"tomorrow|zítra|завтра", t):
        start = today + timedelta(days=1)
    elif re.search(r"weekend|víkend|выходн", t):
        start = today + timedelta(days=(5 - today.weekday()) % 7)
        days = max(days, 2)
    units = "fahrenheit" if re.search(r"fahrenheit|°f\b", t) else "celsius"
    if not cities:
        # запрос про погоду, но город не назван — спросить (рантайм подставит ответ пользователя как location)
        q = {"cs": "Pro které město?", "en": "For which city?"}.get(context.get("lang"), "For which city?")
        if re.search(r"[а-яё]", t):
            q = "Для какого города?"
        return {"_missing": "location", "_question": q, "start_date": start.isoformat(), "forecast_days": days, "units": units}
    items = [{"location": c, "start_date": start.isoformat(), "forecast_days": days, "units": units} for c in cities[:10]]
    return items if len(items) > 1 else items[0]


def format_result(result, lang):
    u = "°F" if result.get("units") == "fahrenheit" else "°C"
    head = {"en": ("Date", "Conditions", "Max", "Min", "Precip.", "Wind max"), "cs": ("Datum", "Počasí", "Max", "Min", "Srážky", "Vítr max")}
    h = head.get(lang, head["en"])
    title = result["location"] + (f", {result['country']}" if result.get("country") else "")
    rows = "".join(f"| {d['date']} | {d['condition']} | {d['t_max']}{u} | {d['t_min']}{u} | "
                   f"{'' if d['precipitation_mm'] is None else str(d['precipitation_mm']) + ' mm'} | "
                   f"{'' if d['wind_max_kmh'] is None else str(d['wind_max_kmh']) + ' km/h'} |\n" for d in result["days"])
    return (f"### {title}\n\n| {' | '.join(h)} |\n|---|---|---|---|---|---|\n{rows}\n_Source: {result.get('source', 'provider')}_")


def check_result(result, params):
    """Проверка без LLM: тот самый город, те самые даты, нужное число дней."""
    problems = []
    if params.get("location") and result.get("requested_location") != params["location"]:
        problems.append("result belongs to another location")
    days = result.get("days") or []
    if params.get("forecast_days") and len(days) != int(params["forecast_days"]):
        problems.append(f"expected {params['forecast_days']} days, got {len(days)}")
    if params.get("start_date") and days and days[0]["date"] != params["start_date"]:
        problems.append(f"forecast starts {days[0]['date']}, requested {params['start_date']}")
    return problems
