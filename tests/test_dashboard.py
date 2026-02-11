"""Tests for the web dashboard."""

import json
import pytest

from spx_bot.dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"SPX 0DTE" in resp.data


def test_api_status_returns_json(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "running" in data
    assert "risk" in data
    assert "positions" in data
    assert "trades" in data


def test_api_history_returns_json(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "days" in data


def test_api_backtest_returns_json(client):
    resp = client.get("/api/backtest")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "error" in data or "results" in data
