"""MangaX'in görünmeyen, yalnız doğrulanmış Windows kurulum yardımcısı.

Bu program ağ erişimi yapmaz. Ana uygulamanın SHA-256 ile doğruladığı installer'ı
bir kez daha doğrular, MangaX kapandıktan sonra sessiz kurar ve yeni sürümü açar.
Planın HMAC anahtarı yalnız anonim stdin borusundan gelir; diske veya komut
satırına yazılmaz.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError("Güvenli olmayan güncelleme yolu")
    return path


def _wait_for_process(pid: int, timeout: float = 90.0) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            return result == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _load_plan(plan_path: Path, key: bytes) -> dict:
    envelope = json.loads(plan_path.read_text(encoding="utf-8"))
    payload = envelope.get("payload")
    signature = str(envelope.get("signature") or "")
    if not isinstance(payload, dict) or not hmac.compare_digest(
        signature,
        hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest(),
    ):
        raise ValueError("Güncelleme planı doğrulanamadı")
    return payload


def apply_plan(plan_path: str, encoded_key: str) -> int:
    path = _safe_path(plan_path)
    key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
    payload = _load_plan(path, key)
    installer = _safe_path(str(payload["installer_path"]))
    install_dir = _safe_path(str(payload["install_dir"]))
    result_path = _safe_path(str(payload["result_path"]))
    edition = str(payload.get("edition") or "")
    target_name = "MangaX-Reader.exe" if edition == "reader" else "MangaX.exe" if edition == "full" else ""
    if not target_name or str(payload.get("target_name")) != target_name:
        raise ValueError("Edition güncelleme hedefi geçersiz")
    if installer.parent != path.parent or installer.suffix.lower() != ".exe":
        raise ValueError("Installer yolu geçersiz")
    if path.parent.parent.name.lower() != "app-update" or path.parent.parent.parent.name.lower() != "mangax":
        raise ValueError("Geçici güncelleme alanı geçersiz")
    if result_path.parent.name.lower() != "data" or result_path.parent.parent.name.lower() != "mangax":
        raise ValueError("Sonuç dosyası hedefi geçersiz")
    expected_size = int(payload["size"])
    expected_hash = str(payload["sha256"]).lower()
    if installer.stat().st_size != expected_size or _sha256(installer) != expected_hash:
        raise ValueError("Installer bütünlük doğrulaması başarısız")
    if not _wait_for_process(int(payload["parent_pid"])):
        raise RuntimeError("MangaX güvenli biçimde kapatılamadı")

    log_path = path.parent / "installer.log"
    command = [
        str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
        f"/DIR={install_dir}", f"/LOG={log_path}",
    ]
    target = install_dir / target_name
    try:
        completed = subprocess.run(
            command,
            cwd=str(path.parent),
            shell=False,
            creationflags=CREATE_NO_WINDOW,
            timeout=600,
            check=False,
        )
        success = completed.returncode == 0 and target.is_file()
    except (OSError, subprocess.SubprocessError):
        success = False
    _write_result(result_path, {
        "status": "completed" if success else "failed",
        "version": str(payload.get("version") or ""),
        "edition": edition,
        "message": "Güncelleme başarıyla kuruldu." if success else "Güncelleme tamamlanamadı.",
    })
    if target.is_file():
        subprocess.Popen(
            [str(target)], cwd=str(install_dir), shell=False,
            close_fds=True, creationflags=CREATE_NEW_PROCESS_GROUP,
        )
    return 0 if success else 1


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--apply-plan":
        return 2
    encoded_key = sys.stdin.buffer.readline(4096).decode("ascii", "strict").strip()
    if not encoded_key:
        return 3
    try:
        return apply_plan(sys.argv[2], encoded_key)
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
