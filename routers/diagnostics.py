"""MangaX çekirdek bileşenleri için kullanıcı tarafından başlatılan tanılama uçları."""
from __future__ import annotations
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from mangax.core.backup_service import validate_backup_payload
from mangax.core.config import APP_VERSION, BACKUPS_DIR, DATA_DIR, DOWNLOADS_DIR, IS_FULL_EDITION, SOURCE_REPORTS_DIR
from mangax.core.database import DB_PATH
from mangax.runtime.startup_metrics import startup_metrics
router = APIRouter(prefix='/api/diagnostics', tags=['Diagnostics'])
DOWNLOAD_QUEUE_FILE = os.path.join(DATA_DIR, 'download_queue.json')
VALID_STATUSES = {'healthy', 'warning', 'broken', 'timeout'}

@router.get('/startup')
def startup_timing_snapshot() -> dict[str, Any]:
    """Return only fixed-name monotonic markers; no user or credential data."""
    return startup_metrics.snapshot()

class DiagnosticResult(BaseModel):
    id: str = Field(max_length=64)
    label: str = Field(max_length=120)
    status: str = Field(max_length=20)
    message: str = Field(max_length=1000)
    details: dict[str, Any] = Field(default_factory=dict)

class SourceDiagnosticResult(BaseModel):
    source_id: str = Field(max_length=64)
    name: str = Field(default='', max_length=120)
    status: str = Field(max_length=20)
    message: str = Field(default='', max_length=1000)
    elapsed_ms: int | None = None

class DiagnosticReportRequest(BaseModel):
    mode: str = Field(default='quick', max_length=20)
    checks: list[DiagnosticResult] = Field(default_factory=list, max_length=50)
    sources: list[SourceDiagnosticResult] = Field(default_factory=list, max_length=100)

def _result(check_id: str, label: str, status: str, message: str, **details: Any) -> dict:
    normalized = status if status in VALID_STATUSES else 'warning'
    return {'id': check_id, 'label': label, 'status': normalized, 'message': message, 'details': details}

def _check_application() -> dict:
    mode = 'Paketlenmiş EXE' if getattr(sys, 'frozen', False) else 'Kaynak modu'
    return _result('application', 'MangaX çalışma ortamı', 'healthy', f'{APP_VERSION} · {mode} · {platform.system()} {platform.release()}', version=APP_VERSION, frozen=bool(getattr(sys, 'frozen', False)))

def _check_database() -> dict:
    connection = None
    try:
        connection = sqlite3.connect(DB_PATH, timeout=5)
        integrity = connection.execute('PRAGMA quick_check').fetchone()
        if not integrity or str(integrity[0]).lower() != 'ok':
            return _result('database', 'Veritabanı ve kütüphane', 'broken', f'SQLite bütünlük kontrolü başarısız: {(integrity[0] if integrity else 'yanıt yok')}')
        manga_count = int(connection.execute('SELECT COUNT(*) FROM mangas').fetchone()[0])
        chapter_count = int(connection.execute('SELECT COUNT(*) FROM downloaded_chapters').fetchone()[0])
        return _result('database', 'Veritabanı ve kütüphane', 'healthy', f'Sağlıklı · {manga_count} manga · {chapter_count} indirilen bölüm', manga_count=manga_count, downloaded_chapter_count=chapter_count)
    except Exception as error:
        return _result('database', 'Veritabanı ve kütüphane', 'broken', f'Veritabanı okunamadı: {error}')
    finally:
        if connection is not None:
            connection.close()

def _check_storage() -> dict:
    probe_path = ''
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        Path(DOWNLOADS_DIR).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='mangax-check-', suffix='.tmp', dir=DATA_DIR, delete=False) as probe:
            probe.write(b'MangaX')
            probe_path = probe.name
        total, used, free = shutil.disk_usage(DOWNLOADS_DIR)
        free_gb = free / 1024 ** 3
        status = 'warning' if free_gb < 1 else 'healthy'
        message = f'Klasörler yazılabilir · {free_gb:.1f} GB boş alan'
        return _result('storage', 'Depolama ve klasör izinleri', status, message, total_bytes=total, used_bytes=used, free_bytes=free)
    except Exception as error:
        return _result('storage', 'Depolama ve klasör izinleri', 'broken', f'Yazma testi başarısız: {error}')
    finally:
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass

def _check_download_queue() -> dict:
    path = Path(DOWNLOAD_QUEUE_FILE)
    if not path.exists():
        return _result('download_queue', 'İndirme kuyruğu', 'healthy', 'Henüz kalıcı indirme kuyruğu oluşturulmamış.', task_count=0)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('kök değer nesne değil')
        queue = payload.get('queue', [])
        tasks = payload.get('tasks', {})
        statuses = payload.get('status', {})
        if not isinstance(queue, list) or not isinstance(tasks, dict) or (not isinstance(statuses, dict)):
            raise ValueError('kuyruk şeması geçersiz')
        active = sum((1 for item in statuses.values() if isinstance(item, dict) and item.get('status') in {'pending', 'downloading', 'paused'}))
        return _result('download_queue', 'İndirme kuyruğu', 'healthy', f'Kuyruk dosyası geçerli · {active} aktif/bekleyen görev', queued_count=len(queue), task_count=len(tasks), active_count=active)
    except Exception as error:
        return _result('download_queue', 'İndirme kuyruğu', 'broken', f'Kuyruk dosyası okunamadı: {error}')

def _check_backups() -> dict:
    backup_dir = Path(BACKUPS_DIR)
    paths = sorted(backup_dir.glob('mangax-auto-*.json'), reverse=True) if backup_dir.is_dir() else []
    invalid = 0
    valid_items = []
    for path in paths:
        try:
            payload = validate_backup_payload(json.loads(path.read_text(encoding='utf-8')))
            valid_items.append({'id': path.name, 'created_at': payload.get('exported_at') or '', 'size_bytes': path.stat().st_size})
        except Exception:
            invalid += 1
    if invalid:
        return _result('backups', 'Yerel yedekler', 'warning', f'{len(valid_items)} sağlam, {invalid} okunamayan yedek bulundu.', valid_count=len(valid_items), invalid_count=invalid)
    if not valid_items:
        return _result('backups', 'Yerel yedekler', 'warning', 'Henüz doğrulanabilir yerel yedek bulunmuyor.', valid_count=0)
    latest = valid_items[0]
    return _result('backups', 'Yerel yedekler', 'healthy', f'{len(valid_items)} sağlam yedek · son kayıt {latest.get('created_at') or 'tarih bilinmiyor'}', valid_count=len(valid_items), latest_id=latest.get('id', ''))


@router.get('/quick')
async def run_quick_diagnostics(local_only: bool=False) -> dict:
    database = _check_database()
    if local_only and database.get('status') == 'healthy':
        manga_count = int((database.get('details') or {}).get('manga_count') or 0)
        database['message'] = f'Sağlıklı · {manga_count} manga'
    checks = [_check_application(), database, _check_storage(), _check_backups()]
    return {'status': 'complete', 'generated_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}

def _clean_text(value: Any) -> str:
    return ' '.join(str(value or '').replace('\x00', '').split())

def build_diagnostic_report(payload: DiagnosticReportRequest) -> str:
    labels = {'healthy': 'SAĞLIKLI', 'warning': 'UYARI', 'broken': 'HATA', 'timeout': 'ZAMAN AŞIMI'}
    generated_at = time.strftime('%Y-%m-%d %H:%M:%S')
    lines = ['MangaX Sistem Kontrolü Raporu', '=' * 31, f'Uygulama sürümü: {APP_VERSION}', f'Tarama türü: {('Tam kontrol' if payload.mode == 'full' else 'Hızlı kontrol')}', f'Rapor zamanı: {generated_at}', '', 'ÇEKİRDEK KONTROLLER', '-' * 20]
    for item in payload.checks:
        lines.append(f'[{labels.get(item.status, item.status.upper())}] {_clean_text(item.label)}: {_clean_text(item.message)}')
    lines.extend(['', 'KAYNAK KONTROLLERİ', '-' * 20])
    if payload.sources:
        for item in payload.sources:
            elapsed = f' · {item.elapsed_ms / 1000:.1f} sn' if item.elapsed_ms is not None else ''
            lines.append(f'[{labels.get(item.status, item.status.upper())}] {_clean_text(item.name) or _clean_text(item.source_id)}: {_clean_text(item.message)}{elapsed}')
    else:
        lines.append('Hızlı kontrolde canlı kaynak testi çalıştırılmadı.')
    lines.extend(['', 'Rapor kişisel manga notlarını, okuma geçmişini veya erişim anahtarlarını içermez.'])
    return '\r\n'.join(lines) + '\r\n'

@router.post('/report')
def save_diagnostic_report(payload: DiagnosticReportRequest) -> dict:
    if not payload.checks:
        raise HTTPException(status_code=400, detail='Önce sistem kontrolünü çalıştırın.')
    report_dir = Path(SOURCE_REPORTS_DIR).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f'mangax-sistem-raporu-{time.strftime('%Y%m%d-%H%M%S')}.txt'
    destination = (report_dir / filename).resolve()
    if report_dir not in destination.parents:
        raise HTTPException(status_code=500, detail='Geçersiz rapor yolu')
    try:
        destination.write_text(build_diagnostic_report(payload), encoding='utf-8-sig', newline='')
    except OSError as error:
        raise HTTPException(status_code=500, detail=f'Rapor kaydedilemedi: {error}')
    return {'status': 'success', 'filename': filename, 'path': str(destination), 'folder': str(report_dir)}
