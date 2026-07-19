"""Edition sınırını koruyan, paket içi MangaX sürüm notları."""
from __future__ import annotations
from typing import Any
RELEASE_NOTES: dict[str, dict[str, Any]] = {'v0.12.4': {'common': ['Başlangıç sihirbazı artık yalnızca ilk kurulumda gösteriliyor.', 'Güncellemeden sonra yeni özellikleri bir kez gösteren sürüm özeti eklendi.'], 'reader': ['MyAnimeList hesabı ve manga listesi Entegrasyonlar bölümünden yönetilebiliyor.']}}

def release_notes_for(version: str, edition: str) -> dict[str, Any] | None:
    entry = RELEASE_NOTES.get(str(version or '').strip())
    normalized_edition = str(edition or '').strip().lower()
    if not isinstance(entry, dict) or normalized_edition not in {'reader', 'full'}:
        return None
    common = [str(item) for item in entry.get('common', []) if str(item).strip()]
    edition_items = [str(item) for item in entry.get(normalized_edition, []) if str(item).strip()]
    if normalized_edition == 'full':
        raise RuntimeError('Reader çevrimiçi servis içermez.')
        edition_items.extend(full_release_notes_for(version))
    items = [*common, *edition_items]
    if not items:
        return None
    return {'version': version, 'edition': normalized_edition, 'items': items}
