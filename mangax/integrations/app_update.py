"""Reader ve Full edition'lar icin dogrulanmis uygulama guncelleme altyapisi."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Protocol

import httpx

from mangax.core.config import APP_EDITION, APP_VERSION, GITHUB_READER_RELEASE_REPOSITORY


MAX_UPDATE_BYTES = 2 * 1024 * 1024 * 1024
UPDATE_TTL_SECONDS = 10 * 60
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
FILENAME_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,199}\.(?:exe|msi)$", re.IGNORECASE)


class AppUpdateError(RuntimeError):
    code = "update_error"
    status_code = 502


class AppUpdateConfirmationRequired(AppUpdateError):
    code = "confirmation_required"
    status_code = 409


class AppUpdateNotReady(AppUpdateError):
    code = "update_not_ready"
    status_code = 409


class AppUpdateIntegrityError(AppUpdateError):
    code = "integrity_failed"
    status_code = 422


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        raise AppUpdateError("Guncelleme surum numarasi gecersiz.")
    return tuple(int(part) for part in match.groups())


def validate_descriptor(payload: dict[str, Any]) -> dict[str, Any]:
    version = str(payload.get("version") or "").strip()
    filename = str(payload.get("filename") or "").strip()
    sha256 = str(payload.get("sha256") or "").strip().lower()
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError) as error:
        raise AppUpdateError("Guncelleme dosya boyutu gecersiz.") from error
    version_tuple(version)
    if Path(filename).name != filename or not FILENAME_PATTERN.fullmatch(filename):
        raise AppUpdateError("Guncelleme dosya adi gecersiz.")
    if size <= 0 or size > MAX_UPDATE_BYTES:
        raise AppUpdateError("Guncelleme dosya boyutu guvenli sinirin disinda.")
    if not SHA256_PATTERN.fullmatch(sha256):
        raise AppUpdateError("Guncelleme SHA-256 degeri gecersiz.")
    result = dict(payload)
    result.update(version=version, filename=filename, size=size, sha256=sha256)
    return result


class UpdateProvider(Protocol):
    channel: str

    def latest(self) -> dict[str, Any]: ...

    def stream(self, descriptor: dict[str, Any]) -> ContextManager[Any]: ...


class PublicReaderUpdateProvider:
    channel = "Reader"

    def __init__(self, repository: str = GITHUB_READER_RELEASE_REPOSITORY):
        self.repository = repository.strip()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MangaX-Reader-Updater",
        }

    def latest(self) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"https://api.github.com/repos/{self.repository}/releases?per_page=20",
                headers=self._headers(), timeout=20.0, follow_redirects=True,
            )
            response.raise_for_status()
            releases = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise AppUpdateError("Reader guncelleme bilgisi alinamadi.") from error
        candidates: list[dict[str, Any]] = []
        for release in releases if isinstance(releases, list) else []:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            version = str(release.get("tag_name") or "").strip()
            try:
                version_tuple(version)
            except AppUpdateError:
                continue
            expected = f"MangaX-Reader-Setup-{version}.exe"
            asset = next((item for item in release.get("assets", []) if item.get("name") == expected), None)
            digest = str((asset or {}).get("digest") or "")
            sha256 = digest.split(":", 1)[1] if digest.lower().startswith("sha256:") else ""
            if asset:
                candidates.append(validate_descriptor({
                    "version": version,
                    "filename": expected,
                    "size": asset.get("size"),
                    "sha256": sha256,
                    "download_url": asset.get("browser_download_url"),
                }))
        if not candidates:
            raise AppUpdateError("Reader icin dogrulanabilir bir guncelleme bulunamadi.")
        return max(candidates, key=lambda item: version_tuple(item["version"]))

    @contextmanager
    def stream(self, descriptor: dict[str, Any]) -> Iterator[Any]:
        url = str(descriptor.get("download_url") or "")
        if not url.startswith(f"https://github.com/{self.repository}/releases/download/"):
            raise AppUpdateError("Reader guncelleme adresi gecersiz.")
        with httpx.stream("GET", url, headers=self._headers(), timeout=60.0, follow_redirects=True) as response:
            response.raise_for_status()
            yield response


class AppUpdateManager:
    def __init__(self, provider: UpdateProvider | None = None, temp_root: str | Path | None = None):
        self.provider = provider or PublicReaderUpdateProvider()
        self.temp_root = Path(temp_root or (Path(tempfile.gettempdir()) / "MangaX" / "app-update")).resolve()
        self._lock = threading.RLock()
        self._updates: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._installer_launcher: Callable[[str], bool] | None = None
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_downloads()

    def set_provider(self, provider: UpdateProvider) -> None:
        with self._lock:
            self.provider = provider
            self._updates.clear()

    def set_installer_launcher(self, launcher: Callable[[str], bool] | None) -> None:
        self._installer_launcher = launcher

    def check(self) -> dict[str, Any]:
        descriptor = validate_descriptor(self.provider.latest())
        available = version_tuple(descriptor["version"]) > version_tuple(APP_VERSION)
        result = {
            "current_version": APP_VERSION,
            "latest_version": descriptor["version"],
            "edition": APP_EDITION,
            "channel": self.provider.channel,
            "update_available": available,
            "filename": descriptor["filename"] if available else "",
            "size": descriptor["size"] if available else 0,
            "sha256": descriptor["sha256"] if available else "",
            "update_id": "",
        }
        if available:
            update_id = secrets.token_urlsafe(24)
            with self._lock:
                now = time.time()
                self._updates = {key: value for key, value in self._updates.items() if now < value["expires_at"]}
                self._updates[update_id] = {**descriptor, "expires_at": now + UPDATE_TTL_SECONDS}
            result["update_id"] = update_id
        return result

    def start_download(self, update_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise AppUpdateConfirmationRequired("Guncelleme icin kullanici onayi gerekli.")
        with self._lock:
            update = self._updates.get(str(update_id or "").strip())
            if not update or time.time() >= update["expires_at"]:
                raise AppUpdateNotReady("Guncelleme bilgisi eskidi. Yeniden kontrol edin.")
            if any(job["status"] in {"downloading", "ready", "installing"} for job in self._jobs.values()):
                raise AppUpdateNotReady("Baska bir guncelleme islemi devam ediyor.")
            job_id = secrets.token_urlsafe(24)
            job_dir = (self.temp_root / job_id).resolve()
            if not job_dir.is_relative_to(self.temp_root) or job_dir == self.temp_root:
                raise AppUpdateError("Gecici guncelleme alani hazirlanamadi.")
            job_dir.mkdir(parents=True, exist_ok=False)
            path = (job_dir / update["filename"]).resolve()
            cancel_event = threading.Event()
            self._jobs[job_id] = {
                **update, "path": path, "status": "downloading", "downloaded": 0,
                "verified": False, "error": "", "cancel_event": cancel_event,
            }
        threading.Thread(target=self._run_download, args=(job_id,), name=f"MangaXUpdate-{job_id[:8]}", daemon=True).start()
        return self.status(job_id)

    def _run_download(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            descriptor = dict(job)
            destination = Path(job["path"])
            cancel_event = job["cancel_event"]
        partial = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with self.provider.stream(descriptor) as response, partial.open("xb") as output:
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    if cancel_event.is_set():
                        raise InterruptedError
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > descriptor["size"]:
                        raise AppUpdateIntegrityError("Guncelleme dosya boyutu eslesmiyor.")
                    digest.update(chunk)
                    output.write(chunk)
                    with self._lock:
                        job["downloaded"] = downloaded
                output.flush()
                os.fsync(output.fileno())
            if downloaded != descriptor["size"] or digest.hexdigest() != descriptor["sha256"]:
                raise AppUpdateIntegrityError("Guncelleme dosyasi dogrulanamadi.")
            os.replace(partial, destination)
            with self._lock:
                job.update(status="ready", verified=True, downloaded=downloaded)
        except InterruptedError:
            self._fail(job_id, "cancelled", "Guncelleme iptal edildi.")
        except AppUpdateIntegrityError as error:
            self._fail(job_id, "failed", str(error))
        except Exception:
            self._fail(job_id, "failed", "Guncelleme guvenli bicimde indirilemedi.")

    def _fail(self, job_id: str, status: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            path = Path(job["path"]) if job and job.get("path") else None
        if path:
            self._safe_remove_tree(path.parent)
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(status=status, verified=False, error=message, path=None)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job:
                raise AppUpdateNotReady("Guncelleme isi bulunamadi.")
            size, downloaded = int(job["size"]), int(job["downloaded"])
            return {
                "job_id": job_id, "status": job["status"], "version": job["version"],
                "filename": job["filename"], "size": size, "downloaded": downloaded,
                "progress": round(downloaded / size * 100, 1) if size else 0.0,
                "verified": bool(job["verified"]), "ready_to_install": job["status"] == "ready" and bool(job["verified"]),
                "error": job["error"],
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job:
                raise AppUpdateNotReady("Guncelleme isi bulunamadi.")
            job["cancel_event"].set()
            ready_path = Path(job["path"]) if job["status"] == "ready" and job.get("path") else None
        if ready_path:
            self._safe_remove_tree(ready_path.parent)
            with self._lock:
                job.update(status="cancelled", verified=False, error="Guncelleme iptal edildi.", path=None)
        return self.status(job_id)

    def install(self, job_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise AppUpdateConfirmationRequired("Kurulum icin kullanici onayi gerekli.")
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job or job["status"] != "ready" or not job["verified"] or not job.get("path"):
                raise AppUpdateNotReady("Dogrulanmis guncelleme dosyasi hazir degil.")
            if self._installer_launcher is None or not Path(job["path"]).is_file():
                raise AppUpdateNotReady("Kurulum masaustu uygulamasindan baslatilamadi.")
            if not self._installer_launcher(str(job["path"])):
                raise AppUpdateNotReady("Guncelleme kurulumu baslatilamadi.")
            job["status"] = "installing"
        return self.status(job_id)

    def cleanup_stale_downloads(self) -> int:
        removed = 0
        if self.temp_root.is_dir():
            for child in self.temp_root.iterdir():
                if child.is_dir() and self._safe_remove_tree(child):
                    removed += 1
                elif child.is_file():
                    try:
                        child.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed

    def _safe_remove_tree(self, target: str | Path) -> bool:
        path = Path(target).resolve()
        if path == self.temp_root or not path.is_relative_to(self.temp_root):
            return False
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


app_update_manager = AppUpdateManager()
