"""Windows kullanıcısına bağlı küçük gizli veri deposu (DPAPI)."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from typing import Any


class SecureStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class WindowsDpapiJsonStore:
    def __init__(self, path: Path, description: str = "MangaX credentials"):
        self.path = Path(path)
        self.description = description

    @staticmethod
    def _ensure_windows() -> None:
        if os.name != "nt":
            raise SecureStoreError("Güvenli hesap saklama bu paketle yalnızca Windows üzerinde destekleniyor.")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        self._ensure_windows()
        try:
            encrypted = base64.b64decode(self.path.read_bytes(), validate=True)
            input_blob, input_buffer = _blob(encrypted)
            output_blob = _DataBlob()
            if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
            ):
                raise SecureStoreError("Windows güvenli hesap verisini çözemedi.")
            try:
                raw = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            finally:
                ctypes.windll.kernel32.LocalFree(output_blob.pbData)
            del input_buffer
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except SecureStoreError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise SecureStoreError("Güvenli hesap verisi okunamadı.") from error

    def save(self, value: dict[str, Any]) -> None:
        self._ensure_windows()
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        input_blob, input_buffer = _blob(raw)
        output_blob = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob), ctypes.c_wchar_p(self.description), None, None, None, 0, ctypes.byref(output_blob)
        ):
            raise SecureStoreError("Windows hesap verisini şifreleyemedi.")
        try:
            encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del input_buffer
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(base64.b64encode(encrypted))
        os.replace(temporary, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise SecureStoreError("Güvenli hesap verisi silinemedi.") from error
