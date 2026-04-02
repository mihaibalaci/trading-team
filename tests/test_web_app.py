"""Tests for web_app.py — API endpoints for agents, strategies, validation, status."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signals"))

import pytest
import json

# Set test env before importing web_app
os.environ.setdefault("FLASK_SECRET", "test-secret")

from web_app import app
from database import init_db, ensure_default_admin


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    init_db()
    ensure_default_admin()
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        data = r.get_json()
        assert data and data.get("ok") is True, f"Login failed: {r.status_code} {r.data[:200]}"
        yield c


# ── Agents ───────────────────────────────────────────────────────

class TestAgentsAPI:
    def test_list_agents(self, client):
        r = client.get("/api/agents")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert len(data) == 8  # larry, kai, clio, mira, finn, sage, remy, cole

    def test_agent_has_fields(self, client):
        r = client.get("/api/agents")
        data = r.get_json()
        for agent in data:
            assert "name" in agent
            assert "desc" in agent
            assert "status" in agent

    def test_all_agents_present(self, client):
        r = client.get("/api/agents")
        names = [a["name"] for a in r.get_json()]
        for expected in ["larry", "kai", "clio", "mira", "finn", "sage", "remy", "cole"]:
            assert expected in names

    def test_agent_action_unknown_agent(self, client):
        r = client.post("/api/agents/nobody/start")
        data = r.get_json()
        assert data["ok"] is False

    def test_agent_action_unknown_action(self, client):
        r = client.post("/api/agents/kai/explode")
        data = r.get_json()
        assert data["ok"] is False

    def test_agent_action_valid(self, client):
        r = client.post("/api/agents/kai/restart")
        data = r.get_json()
        # In standalone mode, returns not-in-service-mode
        assert "ok" in data

    def test_agent_logs(self, client):
        r = client.get("/api/agents/kai/logs")
        assert r.status_code == 200
        data = r.get_json()
        assert "ok" in data
        assert "lines" in data

    def test_agent_logs_unknown(self, client):
        r = client.get("/api/agents/nobody/logs")
        assert r.status_code == 404


# ── Validation ───────────────────────────────────────────────────

class TestValidationAPI:
    def test_validation_endpoint(self, client):
        r = client.get("/api/validation")
        assert r.status_code == 200
        data = r.get_json()
        assert "ok" in data
        assert "results" in data

    def test_validation_returns_list(self, client):
        data = client.get("/api/validation").get_json()
        assert isinstance(data["results"], list)


# ── Strategies ───────────────────────────────────────────────────

class TestStrategiesAPI:
    def test_list_strategies(self, client):
        r = client.get("/api/strategies")
        assert r.status_code == 200
        data = r.get_json()
        assert "horizons" in data
        assert "risks" in data
        assert "assets" in data
        assert "saved" in data

    def test_horizons_structure(self, client):
        data = client.get("/api/strategies").get_json()
        assert len(data["horizons"]) > 0
        h = data["horizons"][0]
        assert "value" in h
        assert "label" in h

    def test_risks_structure(self, client):
        data = client.get("/api/strategies").get_json()
        assert len(data["risks"]) > 0
        r = data["risks"][0]
        assert "risk_pct" in r
        assert "max_pos" in r

    def test_assets_structure(self, client):
        data = client.get("/api/strategies").get_json()
        assert len(data["assets"]) > 0
        a = data["assets"][0]
        assert "watchlist" in a

    def test_save_and_delete_strategy(self, client):
        # Save
        r = client.post("/api/strategies/save", json={
            "name": "test_strat",
            "horizon": "scalp",
            "asset_class": "us_equity",
            "risk_level": "normal",
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True

        # Verify it appears in list
        strats = client.get("/api/strategies").get_json()["saved"]
        test_strat = [s for s in strats if s["name"] == "test_strat"]
        assert len(test_strat) == 1

        # Delete
        r = client.post("/api/strategies/delete", json={"id": test_strat[0]["id"]})
        assert r.status_code == 200


# ── Status ───────────────────────────────────────────────────────

class TestStatusAPI:
    def test_status_endpoint(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "equity" in data
        assert "total_trades" in data
        assert "scanner_running" in data
        assert "broker" in data

    def test_status_broker_info(self, client):
        data = client.get("/api/status").get_json()
        broker = data["broker"]
        assert "platform" in broker
        assert "mode" in broker
        assert "connected" in broker
