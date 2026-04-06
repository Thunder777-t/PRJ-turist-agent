import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class M5SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health_payload_contains_environment(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload.get("status"), "ok")
        self.assertIn("env", payload)

    def test_security_headers_are_set(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")
        self.assertEqual(
            response.headers.get("referrer-policy"),
            "strict-origin-when-cross-origin",
        )
        self.assertEqual(
            response.headers.get("permissions-policy"),
            "geolocation=(), microphone=(), camera=()",
        )
        self.assertEqual(response.headers.get("cache-control"), "no-store")


if __name__ == "__main__":
    unittest.main()
