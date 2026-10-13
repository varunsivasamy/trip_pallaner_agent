from fastapi.testclient import TestClient

from app.main import app


def test_llm_outage_returns_a_clean_failed_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")  # nothing listens here
    with TestClient(app) as client:
        body = client.post("/trips", json={"message": "3 days in Munnar"}).json()
        assert body["stage"] == "failed" and "intake" in body["error"]
        assert "places" not in body and "user_messages" not in body
        assert client.get(f"/trips/{body['trip_id']}").json()["stage"] == "failed"
        assert client.post("/trips/nope/messages", json={"message": "hi"}).status_code == 404
        assert client.post("/trips/nope/confirm-booking", json={"confirm": True}).status_code == 404
