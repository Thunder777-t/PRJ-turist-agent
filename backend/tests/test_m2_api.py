import unittest

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


if __name__ == "__main__":
    unittest.main()
