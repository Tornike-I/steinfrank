import json
import unittest
from datetime import date, timedelta
from unittest import mock

import capability

TODAY = date.today()
CTX = {"today": TODAY.isoformat(), "lang": "en"}


class FakeResp:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self, *a):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    url = req.full_url
    if "name=" in url:
        if "Atlantis" in url:
            return FakeResp({})
        name = capability.urllib.parse.unquote_plus(url.split("name=")[1].split("&")[0])
        return FakeResp({"results": [{"name": name, "country": "Village", "population": 1, "latitude": 1.0, "longitude": 1.0},
                                     {"name": name, "country": "X", "population": 2900000, "feature_code": "PPLC",
                                      "latitude": 50.0, "longitude": 14.0}]})
    start = date.fromisoformat(url.split("start_date=")[1].split("&")[0])
    end = date.fromisoformat(url.split("end_date=")[1].split("&")[0])
    n = (end - start).days + 1
    return FakeResp({"daily": {"time": [(start + timedelta(days=i)).isoformat() for i in range(n)], "weather_code": [61] * n,
                               "temperature_2m_max": [15.0] * n, "temperature_2m_min": [5.0] * n,
                               "precipitation_sum": [1.2] * n, "wind_speed_10m_max": [20.0] * n}})


class ParseTests(unittest.TestCase):
    def test_one_city_tomorrow(self):
        p = capability.parse_request("What's the weather in Prague tomorrow?", CTX)
        self.assertEqual((p["location"], p["start_date"]), ("Prague", (TODAY + timedelta(days=1)).isoformat()))

    def test_two_cities(self):
        self.assertEqual([p["location"] for p in capability.parse_request("Weather in Dubai and Tel Aviv", CTX)], ["Dubai", "Tel Aviv"])

    def test_four_cities_oxford_comma(self):
        p = capability.parse_request("Weather in London, Paris, Berlin, and Rome", CTX)
        self.assertEqual([x["location"] for x in p], ["London", "Paris", "Berlin", "Rome"])

    def test_horizon_words_and_digits(self):
        self.assertEqual(capability.parse_request("Forecast for Berlin for ten days", CTX)["forecast_days"], 10)
        self.assertEqual(capability.parse_request("Weather in Oslo for 7 days", CTX)["forecast_days"], 7)

    def test_spoken_request_with_fillers(self):
        p = capability.parse_request("Give me what the weather in Kiev and, uh, in Prague", CTX)
        self.assertEqual([x["location"] for x in p], ["Kiev", "Prague"])
        p = capability.parse_request("Now, what the weather right now in the Kiev and the Prague right now?", CTX)
        self.assertEqual([x["location"] for x in p], ["Kiev", "Prague"])

    def test_russian_two_cities(self):
        self.assertEqual(len(capability.parse_request("Погода в Киеве и Праге", CTX)), 2)

    def test_no_previous_asks_for_city(self):
        self.assertEqual(capability.parse_request("weather in those same cities", CTX)["_missing"], "location")

    def test_previous_reference(self):
        p = capability.parse_request("weather tomorrow in those same cities", {**CTX, "previous": [{"location": "Kyiv"}, {"location": "Prague"}]})
        self.assertEqual([x["location"] for x in p], ["Kyiv", "Prague"])

    def test_missing_city_asks(self):
        p = capability.parse_request("What's the weather tomorrow?", CTX)
        self.assertEqual((p["_missing"], p["_question"]), ("location", "For which city?"))
        self.assertEqual(p["start_date"], (TODAY + timedelta(days=1)).isoformat())

    def test_not_weather(self):
        self.assertIsNone(capability.parse_request("Prepare me for an interview", CTX))

    def test_fahrenheit(self):
        self.assertEqual(capability.parse_request("Weather in Miami in fahrenheit", CTX)["units"], "fahrenheit")


class RunTests(unittest.TestCase):
    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_run_and_check(self, _):
        r = capability.run({"location": "Kyiv", "start_date": TODAY.isoformat(), "forecast_days": 3})
        self.assertEqual((r["location"], len(r["days"]), r["days"][0]["condition"]), ("Kyiv", 3, "light rain"))
        self.assertEqual(capability.check_result(r, {"location": "Kyiv", "forecast_days": 3, "start_date": TODAY.isoformat()}), [])

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_old_and_inflected_names_resolve_to_the_capital(self, _):
        for name in ("Kiev", "Киеве"):
            r = capability.run({"location": name})
            self.assertEqual((r["location"], r["country"], r["requested_location"]), ("Kyiv", "X", name))

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_check_detects_other_city(self, _):
        r = capability.run({"location": "Dubai", "forecast_days": 1})
        self.assertTrue(capability.check_result(r, {"location": "Kyiv", "forecast_days": 1}))

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_unknown_location(self, _):
        with self.assertRaises(ValueError):
            capability.run({"location": "Atlantis"})

    def test_horizon_limit(self):
        with self.assertRaises(ValueError):
            capability.run({"location": "Oslo", "forecast_days": 40})

    def test_bad_units(self):
        with self.assertRaises(ValueError):
            capability.run({"location": "Oslo", "units": "kelvin"})

    @mock.patch("urllib.request.urlopen", side_effect=capability.urllib.error.URLError("down"))
    def test_provider_down_is_connection_error(self, _):
        with self.assertRaises(ConnectionError):
            capability.run({"location": "Oslo"})

    @mock.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    def test_format_markdown_table(self, _):
        s = capability.format_result(capability.run({"location": "Kyiv", "forecast_days": 2}), "en")
        self.assertIn("### Kyiv", s)
        self.assertIn("| Date |", s)
        self.assertNotEqual(s, capability.format_result(capability.run({"location": "Dubai", "forecast_days": 2}), "en"))


if __name__ == "__main__":
    unittest.main()
