"""Reader ve Full edition'lar icin dogrulanmis uygulama guncelleme altyapisi."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager, Iterator, Protocol
from urllib.parse import urlsplit

import httpx

from mangax.core.config import APP_EDITION, APP_VERSION, DATA_DIR, GITHUB_READER_RELEASE_REPOSITORY


MAX_UPDATE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_SPACE_BUFFER = 128 * 1024 * 1024
UPDATE_TTL_SECONDS = 10 * 60
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
FILENAME_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,199}\.(?:exe|msi)$", re.IGNORECASE)
UPDATE_RESULT_PATH = Path(DATA_DIR) / "app_update_result.json"


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


class AppUpdateNetworkError(AppUpdateError):
    code = "network_error"
    status_code = 503


class AppUpdateDiskSpaceError(AppUpdateError):
    code = "disk_space"
    status_code = 507


class AppUpdateAccessError(AppUpdateError):
    code = "access_denied"
    status_code = 403


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
    notes = payload.get("notes")
    if isinstance(notes, list):
        notes = [str(item).strip()[:300] for item in notes[:8] if str(item).strip()]
    else:
        notes = [line.strip()[:300] for line in str(notes or "").splitlines()[:8] if line.strip()]
    result.update(version=version, filename=filename, size=size, sha256=sha256, notes=notes)
    return result


def validate_update_url(url: str, *, allowed_hosts: set[str]) -> str:
    parsed = urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or host not in allowed_hosts:
        raise AppUpdateError("Guncelleme indirme adresi gecersiz.")
    return parsed.geturl()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_protected_update_plan(
    launch: dict[str, Any], *, install_dir: str | Path, updater_copy: str | Path,
) -> tuple[Path, str]:
    installer = Path(str(launch["path"])).resolve()
    job_dir = installer.parent
    updater = Path(updater_copy).resolve()
    if updater.parent != job_dir or installer.parent != job_dir:
        raise AppUpdateError("Guncelleme yardimcisi guvenli alanda degil.")
    edition = str(launch.get("edition") or APP_EDITION)
    target_name = "MangaX-Reader.exe" if edition == "reader" else "MangaX.exe" if edition == "full" else ""
    if not target_name:
        raise AppUpdateError("Guncelleme edition bilgisi gecersiz.")
    payload = {
        "installer_path": str(installer),
        "install_dir": str(Path(install_dir).resolve()),
        "result_path": str(UPDATE_RESULT_PATH.resolve()),
        "target_name": target_name,
        "edition": edition,
        "version": str(launch["version"]),
        "size": int(launch["size"]),
        "sha256": str(launch["sha256"]),
        "parent_pid": os.getpid(),
    }
    key = secrets.token_bytes(32)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = {"payload": payload, "signature": hmac.new(key, canonical, hashlib.sha256).hexdigest()}
    plan = job_dir / "install-plan.json"
    temporary = job_dir / "install-plan.tmp"
    temporary.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, plan)
    return plan, base64.urlsafe_b64encode(key).decode("ascii")


class UpdateProvider(Protocol):
    channel: str

    def latest(self) -> dict[str, Any]: ...

    def stream(self, descriptor: dict[str, Any], *, offset: int = 0) -> ContextManager[Any]: ...


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
        except httpx.HTTPStatusError as error:
            raise AppUpdateNetworkError("Reader guncelleme hizmeti gecici olarak yanit vermiyor.") from error
        except httpx.HTTPError as error:
            raise AppUpdateNetworkError("Internet baglantisi kurulamadigi icin guncelleme kontrol edilemedi.") from error
        except (ValueError, TypeError) as error:
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
                    "notes": release.get("body") or "",
                }))
        if not candidates:
            raise AppUpdateError("Reader icin dogrulanabilir bir guncelleme bulunamadi.")
        return max(candidates, key=lambda item: version_tuple(item["version"]))

    @contextmanager
    def stream(self, descriptor: dict[str, Any], *, offset: int = 0) -> Iterator[Any]:
        url = str(descriptor.get("download_url") or "")
        if not url.startswith(f"https://github.com/{self.repository}/releases/download/"):
            raise AppUpdateError("Reader guncelleme adresi gecersiz.")
        allowed_hosts = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        validate_update_url(url, allowed_hosts=allowed_hosts)
        headers = self._headers()
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        def guard_redirect(response: httpx.Response) -> None:
            if response.next_request is not None:
                validate_update_url(str(response.next_request.url), allowed_hosts=allowed_hosts)
        with httpx.Client(follow_redirects=True, timeout=60.0, event_hooks={"response": [guard_redirect]}) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                for item in [*response.history, response]:
                    validate_update_url(str(item.url), allowed_hosts=allowed_hosts)
                yield response


class AppUpdateManager:
    def __init__(self, provider: UpdateProvider | None = None, temp_root: str | Path | None = None):
        self.provider = provider or PublicReaderUpdateProvider()
        self.temp_root = Path(temp_root or (Path(tempfile.gettempdir()) / "MangaX" / "app-update")).resolve()
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._updates: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._installer_launcher: Callable[[dict[str, Any]], bool] | None = None
        self._last_result: dict[str, Any] = {}
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_downloads()

    def set_provider(self, provider: UpdateProvider) -> None:
        with self._lock:
            self.provider = provider
            self._updates.clear()

    def set_installer_launcher(self, launcher: Callable[[dict[str, Any]], bool] | None) -> None:
        self._installer_launcher = launcher

    def check(self) -> dict[str, Any]:
        if not self._check_lock.acquire(blocking=False):
            raise AppUpdateNotReady("Guncelleme kontrolu zaten devam ediyor.")
        started = time.monotonic()
        try:
            descriptor = validate_descriptor(self.provider.latest())
        finally:
            self._check_lock.release()
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
            "notes": descriptor.get("notes", []) if available else [],
            "checked_at": utc_now_iso(),
            "check_duration_ms": round((time.monotonic() - started) * 1000),
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
            if any(job["status"] in {"downloading", "verifying", "ready_to_install", "installing", "restarting"} for job in self._jobs.values()):
                raise AppUpdateNotReady("Baska bir guncelleme islemi devam ediyor.")
            job_id = secrets.token_urlsafe(24)
            job_dir = (self.temp_root / job_id).resolve()
            if not job_dir.is_relative_to(self.temp_root) or job_dir == self.temp_root:
                raise AppUpdateError("Gecici guncelleme alani hazirlanamadi.")
            job_dir.mkdir(parents=True, exist_ok=False)
            path = (job_dir / update["filename"]).resolve()
            if shutil.disk_usage(job_dir).free < int(update["size"]) + MIN_FREE_SPACE_BUFFER:
                self._safe_remove_tree(job_dir)
                raise AppUpdateDiskSpaceError("Guncelleme icin yeterli bos disk alani yok.")
            cancel_event = threading.Event()
            self._jobs[job_id] = {
                **update, "path": path, "status": "downloading", "downloaded": 0,
                "verified": False, "error": "", "cancel_event": cancel_event,
                "speed_bps": 0.0, "eta_seconds": None, "started_at": time.monotonic(),
                "can_resume": False,
            }
        threading.Thread(target=self._run_download, args=(job_id,), name=f"MangaXUpdate-{job_id[:8]}", daemon=True).start()
        return self.status(job_id)

    def _run_download(self, job_id: str, *, resume: bool = False) -> None:
        with self._lock:
            job = self._jobs[job_id]
            descriptor = dict(job)
            destination = Path(job["path"])
            cancel_event = job["cancel_event"]
        partial = destination.with_suffix(destination.suffix + ".part")
        offset = partial.stat().st_size if resume and partial.is_file() else 0
        digest = hashlib.sha256()
        downloaded = offset
        if offset:
            with partial.open("rb") as existing:
                for chunk in iter(lambda: existing.read(1024 * 1024), b""):
                    digest.update(chunk)
        sample_time = time.monotonic()
        sample_bytes = downloaded
        try:
            try:
                stream_context = self.provider.stream(descriptor, offset=offset)
            except TypeError:  # Eski/test saglayicilari Range bilmeyebilir.
                stream_context = self.provider.stream(descriptor)
                offset = downloaded = 0
                digest = hashlib.sha256()
            with stream_context as response:
                resumed = offset > 0 and int(getattr(response, "status_code", 200)) == 206
                if offset and not resumed:
                    offset = downloaded = 0
                    digest = hashlib.sha256()
                mode = "ab" if resumed else "wb"
                with partial.open(mode) as output:
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
                        now = time.monotonic()
                        elapsed = max(now - sample_time, 0.001)
                        instant = max(downloaded - sample_bytes, 0) / elapsed
                        with self._lock:
                            previous = float(job.get("speed_bps") or 0)
                            speed = instant if previous <= 0 else (previous * 0.72 + instant * 0.28)
                            remaining = max(int(descriptor["size"]) - downloaded, 0)
                            job.update(
                                downloaded=downloaded,
                                speed_bps=round(speed, 1),
                                eta_seconds=round(remaining / speed) if speed > 1 else None,
                                can_resume=False,
                            )
                        if now - sample_time >= 0.35:
                            sample_time, sample_bytes = now, downloaded
                    output.flush()
                    os.fsync(output.fileno())
            with self._lock:
                job["status"] = "verifying"
            if cancel_event.is_set():
                raise InterruptedError
            if downloaded != descriptor["size"] or digest.hexdigest() != descriptor["sha256"]:
                raise AppUpdateIntegrityError("Guncelleme dosyasi dogrulanamadi.")
            os.replace(partial, destination)
            with self._lock:
                job.update(status="ready_to_install", verified=True, downloaded=downloaded, speed_bps=0.0, eta_seconds=0, can_resume=False)
        except InterruptedError:
            self._fail(job_id, "cancelled", "Guncelleme iptal edildi.")
        except AppUpdateIntegrityError as error:
            self._fail(job_id, "failed", str(error))
        except AppUpdateNetworkError:
            with self._lock:
                job.update(status="failed", verified=False, error="Ag baglantisi kesildi. Indirmeyi surdurebilirsiniz.", can_resume=partial.is_file() and partial.stat().st_size > 0)
        except AppUpdateError as error:
            self._fail(job_id, "failed", str(error))
        except (httpx.HTTPError, OSError):
            with self._lock:
                job.update(status="failed", verified=False, error="Ag baglantisi kesildi. Indirmeyi surdurebilirsiniz.", can_resume=partial.is_file() and partial.stat().st_size > 0)
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
                "verified": bool(job["verified"]), "ready_to_install": job["status"] == "ready_to_install" and bool(job["verified"]),
                "error": job["error"], "speed_bps": float(job.get("speed_bps") or 0),
                "eta_seconds": job.get("eta_seconds"), "can_resume": bool(job.get("can_resume")),
            }

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job or job.get("status") != "failed" or not job.get("can_resume"):
                raise AppUpdateNotReady("Surdurulebilir bir guncelleme bulunamadi.")
            if any(other is not job and other.get("status") in {"downloading", "verifying", "ready_to_install", "installing", "restarting"} for other in self._jobs.values()):
                raise AppUpdateNotReady("Baska bir guncelleme islemi devam ediyor.")
            job.update(status="downloading", error="", can_resume=False)
            job["cancel_event"].clear()
        threading.Thread(target=self._run_download, args=(job_id,), kwargs={"resume": True}, name=f"MangaXUpdate-{job_id[:8]}", daemon=True).start()
        return self.status(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if not job:
                raise AppUpdateNotReady("Guncelleme isi bulunamadi.")
            job["cancel_event"].set()
            ready_path = Path(job["path"]) if job["status"] in {"ready_to_install", "failed"} and job.get("path") else None
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
            if not job or job["status"] != "ready_to_install" or not job["verified"] or not job.get("path"):
                raise AppUpdateNotReady("Dogrulanmis guncelleme dosyasi hazir degil.")
            if self._installer_launcher is None or not Path(job["path"]).is_file():
                raise AppUpdateNotReady("Kurulum masaustu uygulamasindan baslatilamadi.")
            launch_payload = {key: job[key] for key in ("path", "version", "filename", "size", "sha256")}
            launch_payload["edition"] = APP_EDITION
            if not self._installer_launcher(launch_payload):
                raise AppUpdateNotReady("Guncelleme kurulumu baslatilamadi.")
            job["status"] = "restarting"
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

    def last_result(self, *, consume: bool = False) -> dict[str, Any]:
        try:
            payload = json.loads(UPDATE_RESULT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"status": "none"}
        result = {
            "status": str(payload.get("status") or "failed"),
            "version": str(payload.get("version") or ""),
            "edition": str(payload.get("edition") or ""),
            "message": str(payload.get("message") or "Guncelleme sonucu alinamadi."),
        }
        if consume:
            try:
                UPDATE_RESULT_PATH.unlink(missing_ok=True)
            except OSError:
                pass
        return result

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
