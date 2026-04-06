import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app


class M7ProfileApiTests(unittest.TestCase):
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

    def test_get_preferences_requires_auth(self) -> None:
        response = self.client.get("/api/v1/preferences")
        self.assertEqual(response.status_code, 401, response.text)

    def test_get_and_patch_preferences(self) -> None:
        self._register("pref@example.com", "pref_user")
        token = self._login_get_access("pref@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        initial = self.client.get("/api/v1/preferences", headers=headers)
        self.assertEqual(initial.status_code, 200, initial.text)
        data = initial.json()["data"]
        self.assertEqual(data["language"], "en")
        self.assertEqual(data["timezone"], "UTC")
        self.assertEqual(data["budget_level"], "medium")

        payload = {
            "language": "zh",
            "timezone": "Asia/Shanghai",
            "budget_level": "high",
            "interests": ["food", "history"],
            "dietary": ["no_beef"],
            "mobility_notes": "avoid stairs",
        }
        patched = self.client.patch("/api/v1/preferences", json=payload, headers=headers)
        self.assertEqual(patched.status_code, 200, patched.text)
        patched_data = patched.json()["data"]
        self.assertEqual(patched_data["language"], "zh")
        self.assertEqual(patched_data["timezone"], "Asia/Shanghai")
        self.assertEqual(patched_data["budget_level"], "high")
        self.assertEqual(patched_data["interests"], ["food", "history"])
        self.assertEqual(patched_data["dietary"], ["no_beef"])
        self.assertEqual(patched_data["mobility_notes"], "avoid stairs")

        fetched_again = self.client.get("/api/v1/preferences", headers=headers)
        self.assertEqual(fetched_again.status_code, 200, fetched_again.text)
        again_data = fetched_again.json()["data"]
        self.assertEqual(again_data["language"], "zh")
        self.assertEqual(again_data["timezone"], "Asia/Shanghai")
        self.assertEqual(again_data["budget_level"], "high")


if __name__ == "__main__":
    unittest.main()
