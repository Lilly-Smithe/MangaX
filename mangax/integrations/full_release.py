"""Yetkili MangaX Reader kullanıcıları için doğrulanmış Full kurulum akışı."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote

import httpx

from mangax.core.config import GITHUB_ACCESS_REPOSITORY, GITHUB_FULL_RELEASE_MANIFEST_PATH
from mangax.integrations.github_integration import GitHubIntegrationError, github_integration_manager


GITHUB_API_URL = "https://api.github.com"
MAX_FULL_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
MANIFEST_TTL_SECONDS = 10 * 60
VERSION_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FILENAME_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,199}\.(?:exe|msi)$", re.IGNORECASE)


class FullReleaseError(RuntimeError):
    code = "full_release_error"
    status_code = 422


class FullReleaseConfirmationRequired(FullReleaseError):
    code = "confirmation_required"
    status_code = 409


class FullReleaseNotReady(FullReleaseError):
    code = "release_not_ready"
    status_code = 409


class FullReleaseIntegrityError(FullReleaseError):
    code = "integrity_failed"
    status_code = 422


class FullReleaseManager:
    def __init__(
        self,
        *,
        repository: str = GITHUB_ACCESS_REPOSITORY,
        manifest_path: str = GITHUB_FULL_RELEASE_MANIFEST_PATH,
        temp_root: str | Path | None = None,
        token_provider: Callable[[], str] | None = None,
    ):
        self.repository = str(repository or "").strip()
        self.manifest_path = str(manifest_path or "").strip().strip("/")
        self.temp_root = Path(temp_root or (Path(tempfile.gettempdir()) / "MangaX" / "full-release")).resolve()
        self.token_provider = token_provider or github_integration_manager.require_access
        self._lock = threading.RLock()
        self._manifests: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._installer_launcher: Callable[[str], bool] | None = None
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_downloads()

    def set_installer_launcher(self, launcher: Callable[[str], bool] | None) -> None:
        self._installer_launcher = launcher

    @staticmethod
    def _headers(token: str, accept: str) -> dict[str, str]:
        return {
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MangaX-Full-Release",
        }

    def _contents_url(self, repository_path: str) -> str:
        safe_path = quote(str(repository_path).strip("/"), safe="/")
        return f"{GITHUB_API_URL}/repos/{self.repository}/contents/{safe_path}"

    @staticmethod
    def _validate_manifest(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
            raise FullReleaseError("Full sürüm manifesti geçersiz.")
        version = str(payload.get("version") or "").strip()
        asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else {}
        filename = str(asset.get("filename") or "").strip()
        repository_path = str(asset.get("path") or "").strip().strip("/")
        sha256 = str(asset.get("sha256") or "").strip().lower()
        try:
            size = int(asset.get("size") or 0)
        except (TypeError, ValueError) as error:
            raise FullReleaseError("Full sürüm dosya boyutu geçersiz.") from error

        pure_path = PurePosixPath(repository_path)
        if not VERSION_PATTERN.fullmatch(version):
            raise FullReleaseError("Full sürüm numarası geçersiz.")
        if (
            not filename
            or Path(filename).name != filename
            or not FILENAME_PATTERN.fullmatch(filename)
            or Path(filename).suffix.lower() not in {".exe", ".msi"}
            or not repository_path
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or pure_path.name != filename
        ):
            raise FullReleaseError("Full sürüm dosya tanımı geçersiz.")
        if size <= 0 or size > MAX_FULL_RELEASE_BYTES:
            raise FullReleaseError("Full sürüm dosya boyutu güvenli sınırın dışında.")
        if not SHA256_PATTERN.fullmatch(sha256):
            raise FullReleaseError("Full sürüm SHA-256 değeri geçersiz.")
        return {
            "version": version,
            "filename": filename,
            "repository_path": repository_path,
            "size": size,
            "sha256": sha256,
        }

    def latest_manifest(self) -> dict[str, Any]:
        token = self.token_provider()
        try:
            response = httpx.get(
                self._contents_url(self.manifest_path),
                headers=self._headers(token, "application/vnd.github.raw+json"),
                timeout=20.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            manifest = self._validate_manifest(response.json())
        except FullReleaseError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise FullReleaseError("Full sürüm bilgisi GitHub üzerinden alınamadı.") from error

        manifest_id = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._manifests = {
                key: value for key, value in self._manifests.items()
                if now < float(value.get("expires_at") or 0)
            }
            self._manifests[manifest_id] = {**manifest, "expires_at": now + MANIFEST_TTL_SECONDS}
        return self._public_manifest(manifest_id, manifest)

    @staticmethod
    def _public_manifest(manifest_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "manifest_id": manifest_id,
            "version": manifest["version"],
            "filename": manifest["filename"],
            "size": manifest["size"],
            "sha256": manifest["sha256"],
        }

    def start_download(self, manifest_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise FullReleaseConfirmationRequired("İndirme için açık kullanıcı onayı gerekli.")
        with self._lock:
            manifest = self._manifests.get(str(manifest_id or "").strip())
            if not manifest or time.time() >= float(manifest.get("expires_at") or 0):
                raise FullReleaseError("Full sürüm bilgisi güncel değil. Yeniden kontrol edin.")
            if any(job.get("status") in {"downloading", "ready", "installing"} for job in self._jobs.values()):
                raise FullReleaseNotReady("Başka bir Full sürüm işlemi devam ediyor.")
            job_id = secrets.token_urlsafe(24)
            job_dir = (self.temp_root / job_id).resolve()
            if not job_dir.is_relative_to(self.temp_root) or job_dir == self.temp_root:
                raise FullReleaseError("Geçici indirme alanı hazırlanamadı.")
            job_dir.mkdir(parents=True, exist_ok=False)
            destination = (job_dir / manifest["filename"]).resolve()
            if not destination.is_relative_to(job_dir) or destination.parent != job_dir:
                self._safe_remove_tree(job_dir)
                raise FullReleaseError("Geçici indirme hedefi güvenli değil.")
            cancel_event = threading.Event()
            self._jobs[job_id] = {
                "status": "downloading",
                "version": manifest["version"],
                "filename": manifest["filename"],
                "repository_path": manifest["repository_path"],
                "size": manifest["size"],
                "sha256": manifest["sha256"],
                "downloaded": 0,
                "verified": False,
                "error": "",
                "path": destination,
                "cancel_event": cancel_event,
            }
        threading.Thread(
            target=self._run_download,
            args=(job_id,),
            name=f"MangaXFullRelease-{job_id[:8]}",
            daemon=True,
        ).start()
        return self.download_status(job_id)

    def _run_download(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            destination = Path(job["path"])
            partial = destination.with_suffix(destination.suffix + ".part")
            expected_size = int(job["size"])
            expected_sha = str(job["sha256"])
            repository_path = str(job["repository_path"])
            cancel_event = job["cancel_event"]
        digest = hashlib.sha256()
        downloaded = 0
        try:
            token = self.token_provider()
            with httpx.stream(
                "GET",
                self._contents_url(repository_path),
                headers=self._headers(token, "application/vnd.github.raw"),
                timeout=60.0,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                with partial.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 256):
                        if cancel_event.is_set():
                            raise InterruptedError("cancelled")
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > expected_size:
                            raise FullReleaseIntegrityError("İndirilen dosyanın boyutu manifest ile eşleşmiyor.")
                        digest.update(chunk)
                        output.write(chunk)
                        with self._lock:
                            job["downloaded"] = downloaded
                    output.flush()
                    os.fsync(output.fileno())
            if downloaded != expected_size or digest.hexdigest().lower() != expected_sha:
                raise FullReleaseIntegrityError("Full sürüm dosyası doğrulanamadı.")
            os.replace(partial, destination)
            with self._lock:
                job["status"] = "ready"
                job["verified"] = True
                job["downloaded"] = downloaded
        except InterruptedError:
            self._fail_job(job_id, "cancelled", "İndirme iptal edildi.")
        except FullReleaseIntegrityError as error:
            self._fail_job(job_id, "failed", str(error))
        except (GitHubIntegrationError, httpx.HTTPError, OSError) as error:
            del error
            self._fail_job(job_id, "failed", "Full sürüm güvenli biçimde indirilemedi.")
        except Exception as error:
            del error
            self._fail_job(job_id, "failed", "Full sürüm işlemi tamamlanamadı.")

    def _fail_job(self, job_id: str, status: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            path = Path(job["path"])
        # Son durum istemciye ancak geçici dosyalar temizlendikten sonra görünür.
        # Böylece "başarısız" yanıtı alındığı anda çalıştırılabilir artık kalmaz.
        self._safe_remove_tree(path.parent)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = status
            job["verified"] = False
            job["error"] = message
            job["path"] = None

    def download_status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job:
                raise FullReleaseError("Full sürüm indirme işi bulunamadı.")
            total = int(job["size"])
            downloaded = int(job["downloaded"])
            return {
                "job_id": str(job_id),
                "status": str(job["status"]),
                "version": str(job["version"]),
                "filename": str(job["filename"]),
                "size": total,
                "downloaded": downloaded,
                "progress": round((downloaded / total) * 100, 1) if total else 0.0,
                "sha256": str(job["sha256"]),
                "verified": bool(job["verified"]),
                "ready_to_install": bool(job["status"] == "ready" and job["verified"]),
                "error": str(job["error"]),
            }

    def cancel_download(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job:
                raise FullReleaseError("Full sürüm indirme işi bulunamadı.")
            job["cancel_event"].set()
            if job["status"] == "ready":
                path = Path(job["path"])
                job["status"] = "cancelled"
                job["verified"] = False
                job["error"] = "İndirme iptal edildi."
                job["path"] = None
                self._safe_remove_tree(path.parent)
        return self.download_status(job_id)

    def install(self, job_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise FullReleaseConfirmationRequired("Kurulum için açık kullanıcı onayı gerekli.")
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job or job.get("status") != "ready" or not job.get("verified") or not job.get("path"):
                raise FullReleaseNotReady("Doğrulanmış Full kurulum dosyası hazır değil.")
            launcher = self._installer_launcher
            if launcher is None:
                raise FullReleaseNotReady("Kurulum yalnızca MangaX masaüstü uygulamasından başlatılabilir.")
            path = Path(job["path"])
            if not path.is_file():
                raise FullReleaseNotReady("Doğrulanmış Full kurulum dosyası bulunamadı.")
            if not launcher(str(path)):
                raise FullReleaseNotReady("Full kurulum başlatılamadı.")
            job["status"] = "installing"
        return self.download_status(job_id)

    def cleanup_stale_downloads(self) -> int:
        removed = 0
        if not self.temp_root.is_dir():
            return removed
        for child in list(self.temp_root.iterdir()):
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


full_release_manager = FullReleaseManager()
