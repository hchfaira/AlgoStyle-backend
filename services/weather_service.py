"""
Weather service — fetches live weather data via wttr.in (no API key required).

wttr.in is a free, public weather service.  We request JSON format:
  https://wttr.in/{city}?format=j1

Results are cached in-memory for 30 minutes per city to avoid hammering
the API on every outfit recommendation request.
"""
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── In-memory cache: city → (timestamp, payload) ─────────────
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 30 * 60  # 30 minutes
_MAX_CACHE_SIZE = 50   # evict oldest entry when exceeded


def _time_of_day() -> str:
    """Return morning / afternoon / evening / night based on local hour."""
    h = datetime.now().hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "night"


def _condition_label(code: int) -> str:
    """Map wttr.in weather code → simple English label."""
    if code == 113:
        return "sunny" if datetime.now().hour in range(6, 20) else "clear"
    if code in (116, 119, 122):
        return "cloudy"
    if code in (143, 248, 260):
        return "foggy"
    if code in (176, 179, 182, 185, 263, 266, 281, 284,
                293, 296, 299, 302, 305, 308, 311, 314,
                317, 320, 323, 326, 338):
        return "rainy"
    if code in (329, 332, 335, 338, 371, 374, 377):
        return "snowy"
    if code in (200, 386, 389, 392, 395):
        return "stormy"
    return "clear"


async def fetch_weather(city: str = "auto") -> dict:
    """
    Fetch current weather for `city` (or auto-detect from IP when city="auto").
    Returns a dict with:
        temperature_celsius: float
        condition: str          e.g. "sunny", "rainy", "cloudy"
        condition_code: int
        humidity: int
        feels_like_celsius: float
        city_name: str
        time_of_day: str
        cached: bool
    """
    cache_key = city.lower().strip()
    now = time.time()

    # Return cached result if still fresh
    if cache_key in _CACHE:
        ts, payload = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            payload["cached"] = True
            payload["time_of_day"] = _time_of_day()   # refresh time-of-day
            return payload

    try:
        import httpx
        url = f"https://wttr.in/{city}?format=j1"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"User-Agent": "AlgoStyle/1.0"})
            resp.raise_for_status()
            raw = resp.json()

        # wttr.in wraps everything under a "data" key
        data = raw.get("data", raw)

        current = data["current_condition"][0]
        temp_c = float(current["temp_C"])
        feels_c = float(current["FeelsLikeC"])
        humidity = int(current["humidity"])
        code = int(current["weatherCode"])

        # Try to get city name from nearest area
        try:
            city_name = data["nearest_area"][0]["areaName"][0]["value"]
        except Exception:
            city_name = city if city != "auto" else "Your Location"

        payload = {
            "temperature_celsius": round(temp_c, 1),
            "condition": _condition_label(code),
            "condition_code": code,
            "humidity": humidity,
            "feels_like_celsius": round(feels_c, 1),
            "city_name": city_name,
            "time_of_day": _time_of_day(),
            "cached": False,
        }
        _CACHE[cache_key] = (now, payload)
        # Evict oldest entry if cache exceeds max size
        if len(_CACHE) > _MAX_CACHE_SIZE:
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
            del _CACHE[oldest_key]
        logger.info(
            f"Weather fetched for '{city_name}': {temp_c}°C, {payload['condition']}"
        )
        return payload

    except Exception as exc:
        logger.warning(f"Weather fetch failed for '{city}': {exc}")
        # Return a safe default so recommendations still work
        return {
            "temperature_celsius": 20.0,
            "condition": "clear",
            "condition_code": 113,
            "humidity": 50,
            "feels_like_celsius": 20.0,
            "city_name": city if city != "auto" else "Unknown",
            "time_of_day": _time_of_day(),
            "cached": False,
            "error": str(exc),
        }


def fetch_weather_sync(city: str = "auto") -> dict:
    """Synchronous wrapper — runs the async fetch in a new event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — caller should use await fetch_weather()
            raise RuntimeError("Use await fetch_weather() inside async context")
        return loop.run_until_complete(fetch_weather(city))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, fetch_weather(city))
            return future.result(timeout=10)
