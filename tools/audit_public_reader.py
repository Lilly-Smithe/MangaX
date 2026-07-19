"""Üretilmiş MangaX Reader kaynak ağacını public güvenlik sınırına göre denetler."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


FORBIDDEN_TOP_LEVEL = {
    "scrapers",
    "extension_store",
    "data",
    "downloads",
    "kaynak_raporlari",
    ".git",
    ".github",
    ".agents",
    ".codex",
}
FORBIDDEN_FILES = {
    "mangax/full/anilist.py",
    "mangax/full/chapter_tracker.py",
    "mangax/full/dependencies.py",
    "mangax/full/downloader.py",
    "mangax/full/extension_manager.py",
    "mangax/full/image_optimizer.py",
    "mangax/full/mal_integration.py",
    "mangax/full/manga_matcher.py",
    "mangax/full/site_analyzer.py",
    "mangax/full/sources_manager.py",
    "routers/downloads.py",
    "routers/extensions.py",
    "routers/github.py",
    "routers/mal.py",
    "routers/manga.py",
    "routers/news.py",
    "routers/search.py",
    "routers/sources.py",
    "routers/tracker.py",
}
FORBIDDEN_STATIC_FILES = {
    "static/js/discover.js",
    "static/js/downloads.js",
    "static/js/extensions.js",
    "static/js/integrations.js",
    "static/js/notifications.js",
    "static/js/onboarding.js",
    "static/css/modules/discover.css",
    "static/css/modules/downloads.css",
    "static/css/modules/extensions.css",
    "static/css/modules/notifications.css",
}
SENSITIVE_NAME_PATTERNS = (
    re.compile(r"(^|/)(?:\.env(?:\..*)?|credentials?(?:\..*)?|cookies?\.json)$", re.I),
    re.compile(r"\.(?:pem|key|p12|pfx|jks|keystore)$", re.I),
    re.compile(r"(^|/)(?:data|downloads|logs?)(?:/|$)", re.I),
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)(?:client[_ -]?secret|private[_ -]?key|github[_ -]?pat)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
)
FORBIDDEN_ENDPOINT_PREFIXES = (
    "/api/search",
    "/api/download",
    "/api/extensions",
    "/api/sources",
    "/api/news",
    "/api/tracker",
    "/api/mal",
)
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".bat", ".spec"}
PRIVATE_REPOSITORY_REFERENCE = "MangaX-App" + "/mangax-full-releases"
EXTENSION_REPOSITORY_REFERENCE = "MangaX-App" + "/mangax-extensions"
REAL_SOURCE_NAMES = (
    "mangadex", "okutoon", "mangadot", "mangatr", "mangak", "mangasehri",
    "mangacephesi", "okumanga", "uzaymanga", "weebcentral", "asurascans",
    "hayalistic", "golgebahcesi", "tortugaceviri",
)
SCAN_EXCLUSIONS = {
    "tools/audit_public_reader.py",
    "tests/test_public_reader_export.py",
    "PUBLIC_EXPORT_SECURITY.json",
}


def _relative_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _scan_tree(root: Path, files: list[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    top_levels = {Path(item).parts[0] for item in files if Path(item).parts}
    found_top = sorted(top_levels & FORBIDDEN_TOP_LEVEL)
    found_files = sorted(set(files) & (FORBIDDEN_FILES | FORBIDDEN_STATIC_FILES))
    if found_top:
        errors.append("Yasak üst klasörler: " + ", ".join(found_top))
    if found_files:
        errors.append("Yasak Full dosyaları: " + ", ".join(found_files))
    checks.append("Scraper, eklenti mağazası ve Full modül yolu bulunmuyor.")

    sensitive_names = sorted(
        item for item in files
        if any(pattern.search(item) for pattern in SENSITIVE_NAME_PATTERNS)
    )
    if sensitive_names:
        errors.append("Hassas dosya adları: " + ", ".join(sensitive_names))
    checks.append("Hassas dosya adı bulunmuyor.")

    private_reference_locations: list[str] = []
    extension_reference_locations: list[str] = []
    source_name_locations: list[str] = []
    secret_locations: list[str] = []
    endpoint_locations: list[str] = []
    private_runtime_locations: list[str] = []
    for relative in files:
        path = root / relative
        if relative in SCAN_EXCLUSIONS or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lower = text.lower()
        if PRIVATE_REPOSITORY_REFERENCE in text:
            private_reference_locations.append(relative)
        if EXTENSION_REPOSITORY_REFERENCE in text:
            extension_reference_locations.append(relative)
        if any(name in lower for name in REAL_SOURCE_NAMES):
            source_name_locations.append(relative)
        if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS):
            secret_locations.append(relative)
        if any(prefix in text for prefix in FORBIDDEN_ENDPOINT_PREFIXES):
            endpoint_locations.append(relative)
        if path.suffix.lower() == ".py" and any(marker in text for marker in (
            "from mangax.full", "import mangax.full", "from scrapers", "import scrapers",
        )):
            private_runtime_locations.append(relative)

    if private_reference_locations not in ([], ["mangax/core/config.py"]):
        errors.append("Private repo referansı merkezi config dışında: " + ", ".join(private_reference_locations))
    if extension_reference_locations:
        errors.append("Eklenti mağazası repo referansı bulundu: " + ", ".join(extension_reference_locations))
    if source_name_locations:
        errors.append("Gerçek manga kaynağı adı bulundu: " + ", ".join(source_name_locations))
    if secret_locations:
        errors.append("Gömülü token/secret şüphesi: " + ", ".join(secret_locations))
    if endpoint_locations:
        errors.append("Full API endpoint metni bulundu: " + ", ".join(endpoint_locations))
    if private_runtime_locations:
        errors.append("Private runtime importu bulundu: " + ", ".join(private_runtime_locations))
    checks.append("Private repo referansı en fazla merkezi config.py içinde bulunuyor.")
    checks.append("Gerçek kaynak adı, eklenti mağazası repo adresi veya gömülü gizli değer bulunmuyor.")
    checks.append("Keşfet, kaynak, eklenti, takip ve indirme API endpoint metni bulunmuyor.")
    checks.append("Private dependencies, scraper, kaynak veya eklenti runtime importu bulunmuyor.")
    return errors, checks


def _startup_smoke_test(root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mangax-reader-smoke-") as directory:
        smoke_root = Path(directory) / "MangaX"
        shutil.copytree(root, smoke_root)
        runtime = Path(directory) / "runtime"
        script = """
import json
import sys
import main
forbidden = ('/api/search', '/api/download', '/api/extensions', '/api/sources', '/api/news', '/api/tracker', '/api/mal')
def collect_paths(routes):
    paths = []
    for route in routes:
        path = getattr(route, 'path', '')
        if path:
            paths.append(path)
        original = getattr(route, 'original_router', None)
        if original is not None:
            paths.extend(collect_paths(getattr(original, 'routes', ())))
    return paths
paths = sorted(set(collect_paths(main.app.routes)))
bad_paths = [path for path in paths if any(path.startswith(prefix) for prefix in forbidden)]
bad_modules = sorted(name for name in sys.modules if name == 'mangax.full' or name.startswith('mangax.full.') or name == 'scrapers' or name.startswith('scrapers.'))
if bad_paths or bad_modules or '/' not in paths:
    raise SystemExit(json.dumps({'bad_paths': bad_paths, 'bad_modules': bad_modules, 'root_route': '/' in paths}))
print(json.dumps({'edition': 'reader', 'route_count': len(paths), 'root_route': '/' in paths}))
"""
        environment = dict(os.environ)
        environment.update({
            "MANGAX_EDITION": "reader",
            "MANGAX_DATA_DIR": str(runtime / "data"),
            "MANGAX_LOCAL_MANGA_DIR": str(runtime / "local_manga"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(smoke_root),
        })
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=smoke_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return {"ok": False, "detail": (result.stdout + result.stderr).strip()[-1200:]}
        try:
            detail = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = {"output": result.stdout.strip()[-500:]}
        return {"ok": True, "detail": detail}


def audit_public_reader(root: str | Path, *, run_startup: bool = True) -> dict[str, Any]:
    target = Path(root).resolve()
    if not target.is_dir():
        return {"ok": False, "errors": ["Public Reader çıktı klasörü bulunamadı."], "files": []}
    files = _relative_files(target)
    errors, checks = _scan_tree(target, files)
    startup = {"ok": True, "detail": "atlanmış"}
    if run_startup and not errors:
        startup = _startup_smoke_test(target)
        if not startup["ok"]:
            errors.append("Reader bağımsız başlangıç testi başarısız: " + str(startup["detail"]))
    if startup["ok"]:
        checks.append("Reader yalnızca çekirdek routerlarla bağımsız olarak başlatılabiliyor.")
    return {
        "ok": not errors,
        "edition": "reader",
        "file_count": len(files),
        "files": files,
        "checks": checks,
        "errors": errors,
        "startup": startup,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    result = audit_public_reader(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
