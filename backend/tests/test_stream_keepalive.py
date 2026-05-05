from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app


class StreamKeepaliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=cls.engine,
            future=True,
        )
        Base.metadata.create_all(bind=cls.engine)

        def override_get_db():
            db = cls.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=cls.engine)
        cls.engine.dispose()

    def _register_and_login(self) -> tuple[str, str]:
        email = "keepalive@example.com"
        username = "keepalive_user"
        password = "StrongPass123!"

        register = self.client.post(
            "/api/v1/auth/register",
            json={"email": email, "username": username, "password": password},
        )
        self.assertEqual(register.status_code, 200, register.text)

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(login.status_code, 200, login.text)
        access_token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        create = self.client.post(
            "/api/v1/conversations",
            json={"title": "Keepalive test"},
            headers=headers,
        )
        self.assertEqual(create.status_code, 200, create.text)
        conversation_id = create.json()["data"]["id"]
        return headers["Authorization"], conversation_id

    def test_stream_emits_heartbeat_during_long_running_pipeline(self) -> None:
        auth_header, conversation_id = self._register_and_login()

        def fake_stream_events(  # noqa: ANN001
            user_input,
            user_preferences=None,
            conversation_history=None,
            cancel_event=None,
        ):
            _ = user_input
            _ = user_preferences
            _ = conversation_history
            _ = cancel_event
            yield {"type": "message_start", "data": {"understanding": "start"}}
            time.sleep(11.0)
            yield {"type": "message_end", "data": {"response": "done", "final_summary": "done"}}

        events: list[str] = []
        headers = {"Authorization": auth_header}

        with patch("backend.app.api.conversations.stream_assistant_events", side_effect=fake_stream_events):
            with self.client.stream(
                "POST",
                f"/api/v1/conversations/{conversation_id}/stream",
                headers=headers,
                json={"content": "test keepalive"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                        events.append(event_type)
                        if event_type == "message_end":
                            break

        self.assertIn("heartbeat", events)
        self.assertIn("message_end", events)


if __name__ == "__main__":
    unittest.main()
