import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app


class M2ApiTests(unittest.TestCase):
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

    def _register(self, email: str, username: str, password: str = "StrongPass123!") -> None:
        response = self.client.post(
            "/api/v1/auth/register",
            json={"email": email, "username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _login_get_access(self, email: str, password: str = "StrongPass123!") -> str:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["access_token"]

    def _create_conversation(self, token: str, title: str = "Test Conversation") -> str:
        response = self.client.post(
            "/api/v1/conversations",
            json={"title": title},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_register_login_refresh(self) -> None:
        self._register("a@example.com", "user_a")

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "StrongPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        payload = login.json()["data"]
        self.assertIn("access_token", payload)
        self.assertIn("refresh_token", payload)

        refresh = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": payload["refresh_token"]},
        )
        self.assertEqual(refresh.status_code, 200, refresh.text)
        refresh_payload = refresh.json()["data"]
        self.assertIn("access_token", refresh_payload)
        self.assertIn("refresh_token", refresh_payload)

    def test_conversation_user_isolation(self) -> None:
        self._register("u1@example.com", "user_1")
        self._register("u2@example.com", "user_2")

        token_u1 = self._login_get_access("u1@example.com")
        token_u2 = self._login_get_access("u2@example.com")

        c1 = self.client.post(
            "/api/v1/conversations",
            json={"title": "U1 private chat"},
            headers={"Authorization": f"Bearer {token_u1}"},
        )
        self.assertEqual(c1.status_code, 200, c1.text)
        conversation_id = c1.json()["data"]["id"]

        # User 2 should not see user 1 conversation.
        c2_get = self.client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers={"Authorization": f"Bearer {token_u2}"},
        )
        self.assertEqual(c2_get.status_code, 404, c2_get.text)

        c2_list = self.client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token_u2}"},
        )
        self.assertEqual(c2_list.status_code, 200, c2_list.text)
        listed_ids = [item["id"] for item in c2_list.json()["data"]]
        self.assertNotIn(conversation_id, listed_ids)

    def test_message_pipeline_integration(self) -> None:
        self._register("m1@example.com", "msg_user")
        token = self._login_get_access("m1@example.com")
        conversation_id = self._create_conversation(token, "Pipeline Chat")

        with patch(
            "backend.app.api.conversations.generate_assistant_reply",
            return_value="Mocked assistant itinerary response.",
        ):
            response = self.client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "Plan a Kyoto trip"},
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["assistant_content"], "Mocked assistant itinerary response.")

        history = self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(history.status_code, 200, history.text)
        messages = history.json()["data"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")

    def test_stream_message_endpoint(self) -> None:
        self._register("s1@example.com", "stream_user")
        token = self._login_get_access("s1@example.com")
        conversation_id = self._create_conversation(token, "Streaming Chat")

        mock_events = [
            {"type": "message_start", "data": {"input": "hi"}},
            {"type": "planner", "data": {"plan_count": 2}},
            {"type": "token", "data": {"text": "Hello "}},
            {"type": "token", "data": {"text": "world"}},
            {"type": "message_end", "data": {"response": "Hello world"}},
        ]

        with patch(
            "backend.app.api.conversations.stream_assistant_events",
            return_value=iter(mock_events),
        ):
            with self.client.stream(
                "POST",
                f"/api/v1/conversations/{conversation_id}/stream",
                json={"content": "Plan a trip"},
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())

        self.assertIn("event: message_start", body)
        self.assertIn("event: token", body)
        self.assertIn("event: message_end", body)
        self.assertIn("event: persisted", body)


if __name__ == "__main__":
    unittest.main()
