import os
import unittest


os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:3000/api/auth/google/callback",
)

from app import dependencies  # noqa: E402


class DependencyInitializationTests(unittest.TestCase):
    def test_model_backed_services_are_lazy(self) -> None:
        self.assertIsNone(dependencies.file_manager_instance)


if __name__ == "__main__":
    unittest.main()
