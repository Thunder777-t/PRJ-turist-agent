import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app


class M8PersonalizationTests(unittest.TestCase):
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

    def test_message_endpoint_passes_user_preferences_to_pipeline(self) -> None:
        self._register("pmsg@example.com", "pref_msg_user")
        token = self._login_get_access("pmsg@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = self._create_conversation(token)

        patch_pref = self.client.patch(
            "/api/v1/preferences",
            headers=headers,
            json={
                "language": "zh",
                "timezone": "Asia/Shanghai",
                "budget_level": "high",
                "interests": ["food", "museum"],
                "dietary": ["no_beef"],
                "mobility_notes": "avoid stairs",
            },
        )
        self.assertEqual(patch_pref.status_code, 200, patch_pref.text)

        with patch(
            "backend.app.api.conversations.generate_assistant_reply",
            return_value="Personalized response",
        ) as mocked_generate:
            response = self.client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=headers,
                json={"content": "Plan my trip to Kyoto"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        mocked_generate.assert_called_once()
        args = mocked_generate.call_args[0]
        self.assertEqual(args[0], "Plan my trip to Kyoto")
        self.assertEqual(args[1]["language"], "zh")
        self.assertEqual(args[1]["timezone"], "Asia/Shanghai")
        self.assertEqual(args[1]["budget_level"], "high")
        self.assertEqual(args[1]["interests"], ["food", "museum"])
        self.assertEqual(args[1]["dietary"], ["no_beef"])

    def test_stream_endpoint_passes_user_preferences_to_pipeline(self) -> None:
        self._register("pstream@example.com", "pref_stream_user")
        token = self._login_get_access("pstream@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = self._create_conversation(token)

        patch_pref = self.client.patch(
            "/api/v1/preferences",
            headers=headers,
            json={
                "language": "en",
                "timezone": "UTC",
                "budget_level": "low",
                "interests": ["hiking"],
                "dietary": ["vegetarian"],
                "mobility_notes": "",
            },
        )
        self.assertEqual(patch_pref.status_code, 200, patch_pref.text)

        mock_events = [
            {"type": "message_start", "data": {"input": "test", "preferences_applied": True}},
            {"type": "token", "data": {"text": "Hi"}},
            {"type": "message_end", "data": {"response": "Hi"}},
        ]

        with patch(
            "backend.app.api.conversations.stream_assistant_events",
            return_value=iter(mock_events),
        ) as mocked_stream:
            with self.client.stream(
                "POST",
                f"/api/v1/conversations/{conversation_id}/stream",
                headers=headers,
                json={"content": "Plan budget trip"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                _ = "".join(response.iter_text())

        mocked_stream.assert_called_once()
        args = mocked_stream.call_args[0]
        self.assertEqual(args[0], "Plan budget trip")
        self.assertEqual(args[1]["budget_level"], "low")
        self.assertEqual(args[1]["interests"], ["hiking"])
        self.assertEqual(args[1]["dietary"], ["vegetarian"])


if __name__ == "__main__":
    unittest.main()
