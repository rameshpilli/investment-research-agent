#!/usr/bin/env python3
"""Step 2 Test: API Layer — FastAPI health, tickers, error handling."""

from src.tests.helpers import section, check, show, report


def main():
    from fastapi.testclient import TestClient
    from src.api import app
    client = TestClient(app)

    section("API — Health")
    r = client.get("/health")
    check(f"GET /health -> {r.status_code}", r.status_code == 200)
    check("status=ok", r.json()["status"] == "ok")

    section("API — Tickers")
    r = client.get("/tickers")
    check(f"GET /tickers -> {r.status_code}", r.status_code == 200)
    tickers = r.json()["tickers"]
    check("SOC US present", "SOC US" in tickers)
    check("AKSO NO present", "AKSO NO" in tickers)
    show("Tickers", tickers)

    section("API — Bad Ticker")
    r = client.post("/research/run", json={"ticker": "FAKE_XYZ"})
    body = r.json()
    is_error = r.status_code == 400 or (r.status_code == 200 and body.get("success") is False)
    check(f"Bad ticker -> error (status={r.status_code})", is_error)

    r = client.post("/research/question", json={"ticker": "FAKE_XYZ", "question": "test"})
    check(f"Bad ticker question -> 400", r.status_code == 400)

    report()

if __name__ == "__main__":
    main()
