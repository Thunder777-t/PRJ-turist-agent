import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app


class M9ConversationManagementTests(unittest.TestCase):
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

    def _create_conversation(self, token: str, title: str) -> str:
        response = self.client.post(
            "/api/v1/conversations",
            json={"title": title},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_patch_conversation_title_and_archive(self) -> None:
        self._register("m9_a@example.com", "m9_user_a")
        token = self._login_get_access("m9_a@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = self._create_conversation(token, "Original title")

        patch_title = self.client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"title": "Renamed title"},
            headers=headers,
        )
        self.assertEqual(patch_title.status_code, 200, patch_title.text)
        self.assertEqual(patch_title.json()["data"]["title"], "Renamed title")
        self.assertFalse(patch_title.json()["data"]["is_archived"])

        patch_archive = self.client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"is_archived": True},
            headers=headers,
        )
        self.assertEqual(patch_archive.status_code, 200, patch_archive.text)
        self.assertTrue(patch_archive.json()["data"]["is_archived"])

        invalid_patch = self.client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={},
            headers=headers,
        )
        self.assertEqual(invalid_patch.status_code, 422, invalid_patch.text)

    def test_list_conversations_supports_archive_filter_and_search(self) -> None:
        self._register("m9_b@example.com", "m9_user_b")
        token = self._login_get_access("m9_b@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        id_kyoto = self._create_conversation(token, "Kyoto Trip Plan")
        id_osaka = self._create_conversation(token, "Osaka Food Guide")

        archive = self.client.patch(
            f"/api/v1/conversations/{id_osaka}",
            json={"is_archived": True},
            headers=headers,
        )
        self.assertEqual(archive.status_code, 200, archive.text)

        default_list = self.client.get("/api/v1/conversations?limit=100", headers=headers)
        self.assertEqual(default_list.status_code, 200, default_list.text)
        default_ids = [item["id"] for item in default_list.json()["data"]]
        self.assertIn(id_kyoto, default_ids)
        self.assertNotIn(id_osaka, default_ids)

        include_archived = self.client.get(
            "/api/v1/conversations?limit=100&include_archived=true",
            headers=headers,
        )
        self.assertEqual(include_archived.status_code, 200, include_archived.text)
        include_ids = [item["id"] for item in include_archived.json()["data"]]
        self.assertIn(id_kyoto, include_ids)
        self.assertIn(id_osaka, include_ids)

        search = self.client.get(
            "/api/v1/conversations?limit=100&include_archived=true&q=Food",
            headers=headers,
        )
        self.assertEqual(search.status_code, 200, search.text)
        search_ids = [item["id"] for item in search.json()["data"]]
        self.assertNotIn(id_kyoto, search_ids)
        self.assertIn(id_osaka, search_ids)


if __name__ == "__main__":
    unittest.main()
