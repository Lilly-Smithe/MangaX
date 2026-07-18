import json
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from mangax.core.backup_service import build_backup_payload, create_local_backup, import_portable_library, list_local_backups, load_local_backup_settings, local_backup_manager, prune_local_backups, read_local_backup, validate_backup_payload
from mangax.core.config import IS_FULL_EDITION
from mangax.core.dependencies import library_manager
from mangax.core.preferences_manager import preferences_manager
router = APIRouter(prefix='/api/backup', tags=['Backup'])

class BackupEnvelope(BaseModel):
    backup: dict[str, Any]

class LocalBackupSettingsEnvelope(BaseModel):
    enabled: bool = True
    interval_minutes: int = 30
    retention_count: int = 5
    client_settings: dict[str, Any] = Field(default_factory=dict)

class LocalBackupCreateEnvelope(BaseModel):
    client_settings: dict[str, Any] = Field(default_factory=dict)

def _sync_registered_sources() -> None:
    return


async def _import_backup(payload: dict) -> dict:
    backup = validate_backup_payload(payload)
    library_result = import_portable_library(library_manager, backup.get('library') or [])
    custom_imported = 0
    requested_extensions = []
    extension_results = {}
    tracking_settings = {}
    if isinstance(backup.get('app_preferences'), dict):
        preferences_manager.update(backup['app_preferences'])
    extension_success = sum((bool(item.get('success')) for item in extension_results.values()))
    return {**library_result, 'custom_sources_imported': custom_imported, 'extensions_requested': len(requested_extensions), 'extensions_installed': extension_success, 'extension_results': extension_results, 'client_settings': backup.get('client_settings') or {}, 'chapter_tracker_settings': tracking_settings}

@router.get('/export')
def export_backup() -> dict:
    return build_backup_payload()

@router.post('/import')
async def import_backup(envelope: BackupEnvelope) -> dict:
    try:
        result = await _import_backup(envelope.backup)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    current_settings = load_local_backup_settings()
    local_backup_manager.update_settings({**current_settings, 'client_settings': result.get('client_settings') or {}})
    return {'status': 'success', 'message': 'Kütüphane ve okuma geçmişi geri yüklendi.', **result}

@router.get('/local')
def local_backup_overview() -> dict:
    return {'status': 'success', 'settings': load_local_backup_settings(), 'backups': list_local_backups(), 'manager': local_backup_manager.status()}

@router.put('/local/settings')
def update_local_backup_settings(envelope: LocalBackupSettingsEnvelope) -> dict:
    settings = local_backup_manager.update_settings(envelope.model_dump())
    prune_local_backups(settings['retention_count'])
    return {'status': 'success', 'settings': settings}

@router.post('/local/create')
def create_local_backup_now(envelope: LocalBackupCreateEnvelope) -> dict:
    item = local_backup_manager.create('manual', envelope.client_settings)
    if not item:
        raise HTTPException(status_code=500, detail=local_backup_manager.last_error or 'Yerel yedek oluşturulamadı.')
    return {'status': 'success', 'message': 'Yerel yedek oluşturuldu.', 'backup': item}

@router.post('/local/{backup_id}/restore')
async def restore_local_backup(backup_id: str) -> dict:
    try:
        backup = read_local_backup(backup_id)
        create_local_backup('before-restore')
        result = await _import_backup(backup)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    current_settings = load_local_backup_settings()
    local_backup_manager.update_settings({**current_settings, 'client_settings': result.get('client_settings') or {}})
    return {'status': 'success', 'message': 'Yerel yedek geri yüklendi.', **result}
