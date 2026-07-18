import json
import tempfile
import unittest
from pathlib import Path


class PublicReaderExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.is_exported_package = (cls.project_root / "PUBLIC_EXPORT_MANIFEST.json").is_file()
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

    def test_export_manifest_declares_allowlist_policy(self):
        manifest = json.loads((self.output / "PUBLIC_EXPORT_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["edition"], "reader")
        self.assertEqual(manifest["source_policy"], "allowlist")
        self.assertGreater(len(manifest["files"]), 20)

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


if __name__ == "__main__":
    unittest.main()
