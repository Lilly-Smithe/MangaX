import tempfile
import unittest
from pathlib import Path


class PublicReaderExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.is_exported_package = not (cls.project_root / "tools" / "export_public_reader.py").is_file()
        if cls.is_exported_package:
            cls.output = cls.project_root
            cls.temporary = None
        else:
            from tools.export_public_reader import export_public_reader

            cls.temporary = tempfile.TemporaryDirectory()
            cls.output = Path(cls.temporary.name) / "MangaX"
            export_public_reader(cls.output)

    @classmethod
    def tearDownClass(cls):
        if cls.temporary is not None:
            cls.temporary.cleanup()

    def test_internal_export_artifacts_are_not_published(self):
        internal_artifacts = {
            ".mangax-public-reader-export",
            "PUBLIC_EXPORT_MANIFEST.json",
            "PUBLIC_EXPORT_SECURITY.json",
        }
        self.assertTrue(all(not (self.output / name).exists() for name in internal_artifacts))
        self.assertGreater(len([path for path in self.output.rglob("*") if path.is_file()]), 20)

    def test_security_audit_passes_and_reader_starts_independently(self):
        from tools.audit_public_reader import audit_public_reader

        report = audit_public_reader(self.output, run_startup=True)
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["startup"]["ok"], report["startup"])
        self.assertTrue(report["startup"]["detail"]["root_route"])

    def test_full_source_files_and_sensitive_files_are_physically_absent(self):
        from tools.audit_public_reader import FORBIDDEN_FILES, FORBIDDEN_STATIC_FILES, FORBIDDEN_TOP_LEVEL

        files = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        self.assertTrue(files.isdisjoint(FORBIDDEN_FILES | FORBIDDEN_STATIC_FILES))
        self.assertTrue({Path(item).parts[0] for item in files}.isdisjoint(FORBIDDEN_TOP_LEVEL))

    def test_koharu_and_translation_integration_leave_no_reader_trace(self):
        excluded = {
            "tools/audit_public_reader.py",
            "tests/test_public_reader_export.py",
        }
        markers = (
            "koharu",
            "feature_extensions",
            "feature-extensions",
            "reader-translation",
            "/api/translation",
            "manga çevirmeni",
        )
        hits = []
        for path in self.output.rglob("*"):
            relative = path.relative_to(self.output).as_posix()
            if (
                not path.is_file()
                or relative in excluded
                or path.suffix.lower()
                not in {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".bat", ".spec"}
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(marker in text for marker in markers):
                hits.append(relative)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
