from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_public_credentials_are_templates_only(self) -> None:
        self.assertTrue((ROOT / ".env.example").is_file())
        self.assertTrue((ROOT / "gcp-credentials.json.template").is_file())
        tracked_files = subprocess.run(
            ["git", "ls-files", ".env", ".env.production", "gcp-credentials.json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        self.assertEqual(tracked_files, "")

    def test_static_site_build_contains_public_pages(self) -> None:
        with tempfile.TemporaryDirectory():
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "build-pages.sh")],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("Built static site", result.stdout)
            for filename in ("index.html", "terms.html", "privacy.html", "home.css", "home.js"):
                self.assertTrue((ROOT / "dist" / filename).is_file())


if __name__ == "__main__":
    unittest.main()
