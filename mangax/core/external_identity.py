"""MAL ve AniList kimliklerini mevcut manga anahtarlarını değiştirmeden eşler."""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from mangax.core.database import db


IDENTITY_PROVIDERS = frozenset({"myanimelist", "anilist"})
IDENTITY_CONFIDENCES = frozenset({"exact", "high", "low", "manual"})
AUTHORITATIVE_CONFIDENCES = frozenset({"exact", "manual"})
_CONFIDENCE_RANK = {"low": 0, "high": 1, "exact": 2, "manual": 3}
_PROVIDER_ALIASES = {
    "mal": "myanimelist",
    "myanimelist": "myanimelist",
    "anilist": "anilist",
}


class IdentityConflictError(RuntimeError):
    """Kimlik ilişkisi insan kararı olmadan güvenle kurulamadığında yükselir."""

    def __init__(self, message: str, *, conflict_key: str = "") -> None:
        super().__init__(message)
        self.conflict_key = conflict_key


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    manga_id: str = ""
    confidence: str = ""
    match_method: str = ""
    conflict: bool = False
    candidates: tuple[str, ...] = ()
    conflict_key: str = ""


class ExternalIdentityService:
    """Sağlayıcı kimliklerini transaction güvenli biçimde indeksler.

    Başlığa dayalı ilişkiler ``high``/``low`` olarak saklanabilir ancak varsayılan
    çözümleme yalnız kesin veya elle doğrulanmış ilişkileri kullanır.
    """

    def __init__(self, database_manager=db) -> None:
        self.database = database_manager

    @staticmethod
    def _provider(value: Any) -> str:
        provider = _PROVIDER_ALIASES.get(str(value or "").strip().lower(), "")
        if provider not in IDENTITY_PROVIDERS:
            raise ValueError("Desteklenmeyen dış kimlik sağlayıcısı")
        return provider

    @staticmethod
    def _external_id(value: Any) -> str:
        try:
            external_id = int(value or 0)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Geçersiz dış manga kimliği") from error
        if external_id <= 0:
            raise ValueError("Geçersiz dış manga kimliği")
        return str(external_id)

    def _connection(self, conn):
        return nullcontext(conn) if conn is not None else self.database.get_connection()

    @staticmethod
    def _commit_if_owned(conn, supplied_conn) -> None:
        if supplied_conn is None:
            conn.commit()

    @staticmethod
    def _candidate_rows(conn, provider: str, external_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in conn.execute(
                """
                SELECT identity.*
                FROM manga_external_identities AS identity
                JOIN mangas ON mangas.id = identity.manga_id
                WHERE identity.provider = ? AND identity.external_id = ?
                ORDER BY CASE identity.confidence
                    WHEN 'manual' THEN 4 WHEN 'exact' THEN 3
                    WHEN 'high' THEN 2 ELSE 1 END DESC,
                    identity.updated_at DESC, identity.manga_id
                """,
                (provider, external_id),
            ).fetchall()
        ]

    @staticmethod
    def _manga_exists(conn, manga_id: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM mangas WHERE id = ?", (manga_id,)
        ).fetchone() is not None

    def _record_conflict(
        self,
        conn,
        *,
        conflict_key: str,
        conflict_type: str,
        provider: str = "",
        external_id: str = "",
        manga_ids: list[str] | tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        normalized_ids = sorted({str(value) for value in manga_ids if str(value)})
        conn.execute(
            """
            INSERT INTO manga_identity_conflicts (
                conflict_key, conflict_type, provider, external_id, manga_ids,
                details, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ON CONFLICT(conflict_key) DO UPDATE SET
                conflict_type = excluded.conflict_type,
                provider = excluded.provider,
                external_id = excluded.external_id,
                manga_ids = excluded.manga_ids,
                details = excluded.details,
                status = 'open',
                updated_at = excluded.updated_at
            """,
            (
                conflict_key,
                conflict_type,
                provider,
                external_id,
                json.dumps(normalized_ids, ensure_ascii=False),
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        return conflict_key

    def record_conflict(
        self,
        *,
        conflict_key: str,
        conflict_type: str,
        provider: str = "",
        external_id: Any = "",
        manga_ids: list[str] | tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
        conn=None,
    ) -> str:
        supplied = conn
        with self._connection(conn) as active:
            key = self._record_conflict(
                active,
                conflict_key=conflict_key,
                conflict_type=conflict_type,
                provider=str(provider or ""),
                external_id=str(external_id or ""),
                manga_ids=manga_ids,
                details=details,
            )
            self._commit_if_owned(active, supplied)
            return key

    def find(
        self,
        provider: str,
        external_id: Any,
        *,
        allow_fuzzy: bool = False,
        conn=None,
    ) -> IdentityResolution:
        safe_provider = self._provider(provider)
        safe_external_id = self._external_id(external_id)
        supplied = conn
        with self._connection(conn) as active:
            rows = self._candidate_rows(active, safe_provider, safe_external_id)
            authoritative = [
                row for row in rows
                if row["confidence"] in AUTHORITATIVE_CONFIDENCES
            ]
            usable = authoritative
            if not usable and allow_fuzzy:
                usable = [row for row in rows if row["confidence"] == "high"]
            candidates = tuple(sorted({str(row["manga_id"]) for row in usable}))
            if len(candidates) > 1:
                key = self._record_conflict(
                    active,
                    conflict_key=f"duplicate:{safe_provider}:{safe_external_id}",
                    conflict_type=(
                        "anilist_multiple_mal_records"
                        if safe_provider == "anilist"
                        else "duplicate_external_id"
                    ),
                    provider=safe_provider,
                    external_id=safe_external_id,
                    manga_ids=list(candidates),
                )
                self._commit_if_owned(active, supplied)
                return IdentityResolution(
                    conflict=True,
                    candidates=candidates,
                    conflict_key=key,
                )
            if not candidates:
                return IdentityResolution(
                    candidates=tuple(sorted({str(row["manga_id"]) for row in rows}))
                )
            selected = next(row for row in usable if row["manga_id"] == candidates[0])
            return IdentityResolution(
                manga_id=candidates[0],
                confidence=str(selected["confidence"]),
                match_method=str(selected["match_method"]),
                candidates=candidates,
            )

    def resolve(
        self,
        *,
        mal_id: Any = 0,
        anilist_id: Any = 0,
        exact_pair: bool = False,
        conn=None,
    ) -> IdentityResolution:
        """MAL kimliğini birincil tutar; AniList yalnız kesin çiftte devreye girer."""
        mal = self.find("myanimelist", mal_id, conn=conn) if int(mal_id or 0) > 0 else IdentityResolution()
        if mal.conflict:
            return mal
        ani = (
            self.find("anilist", anilist_id, conn=conn)
            if exact_pair and int(anilist_id or 0) > 0
            else IdentityResolution()
        )
        if ani.conflict:
            return ani
        if mal.manga_id and ani.manga_id and mal.manga_id != ani.manga_id:
            supplied = conn
            with self._connection(conn) as active:
                key = self._record_conflict(
                    active,
                    conflict_key=f"pair:mal:{int(mal_id)}:anilist:{int(anilist_id)}",
                    conflict_type="cross_provider_mismatch",
                    provider="myanimelist+anilist",
                    external_id=f"{int(mal_id)}:{int(anilist_id)}",
                    manga_ids=[mal.manga_id, ani.manga_id],
                )
                self._commit_if_owned(active, supplied)
            return IdentityResolution(
                conflict=True,
                candidates=tuple(sorted({mal.manga_id, ani.manga_id})),
                conflict_key=key,
            )
        return mal if mal.manga_id else ani

    def link(
        self,
        manga_id: str,
        provider: str,
        external_id: Any,
        *,
        confidence: str = "exact",
        match_method: str = "provider_id",
        verified: bool | None = None,
        conn=None,
    ) -> IdentityResolution:
        safe_manga_id = str(manga_id or "").strip()
        safe_provider = self._provider(provider)
        safe_external_id = self._external_id(external_id)
        safe_confidence = str(confidence or "").strip().lower()
        if safe_confidence not in IDENTITY_CONFIDENCES:
            raise ValueError("Geçersiz kimlik güven seviyesi")
        safe_method = str(match_method or "provider_id").strip()[:80]
        if "title" in safe_method.lower() and safe_confidence in {"exact", "manual"}:
            safe_confidence = "high"
        if verified is None:
            verified = safe_confidence in AUTHORITATIVE_CONFIDENCES
        supplied = conn
        with self._connection(conn) as active:
            if not self._manga_exists(active, safe_manga_id):
                raise ValueError("Kimliği bağlanacak manga bulunamadı")
            existing_rows = self._candidate_rows(active, safe_provider, safe_external_id)
            foreign_ids = sorted({
                str(row["manga_id"])
                for row in existing_rows
                if str(row["manga_id"]) != safe_manga_id
                and row["confidence"] in AUTHORITATIVE_CONFIDENCES
            })
            if foreign_ids and safe_confidence in AUTHORITATIVE_CONFIDENCES:
                candidates = sorted({safe_manga_id, *foreign_ids})
                key = self._record_conflict(
                    active,
                    conflict_key=f"duplicate:{safe_provider}:{safe_external_id}",
                    conflict_type=(
                        "anilist_multiple_mal_records"
                        if safe_provider == "anilist"
                        else "duplicate_external_id"
                    ),
                    provider=safe_provider,
                    external_id=safe_external_id,
                    manga_ids=candidates,
                )
                self._commit_if_owned(active, supplied)
                raise IdentityConflictError(
                    "Dış kimlik birden fazla manga kaydıyla çakışıyor.",
                    conflict_key=key,
                )

            other_target_ids = sorted({
                str(row[0]) for row in active.execute(
                    """
                    SELECT external_id FROM manga_external_identities
                    WHERE manga_id = ? AND provider = ?
                      AND external_id != ?
                      AND confidence IN ('exact', 'manual')
                    """,
                    (safe_manga_id, safe_provider, safe_external_id),
                ).fetchall()
            })
            if other_target_ids and safe_confidence in AUTHORITATIVE_CONFIDENCES:
                key = self._record_conflict(
                    active,
                    conflict_key=f"manga-provider:{safe_manga_id}:{safe_provider}",
                    conflict_type="manga_provider_identity_conflict",
                    provider=safe_provider,
                    external_id=safe_external_id,
                    manga_ids=[safe_manga_id],
                    details={"existing_external_ids": other_target_ids},
                )
                self._commit_if_owned(active, supplied)
                raise IdentityConflictError(
                    "Manga kaydı aynı sağlayıcıda farklı bir dış kimliğe bağlı.",
                    conflict_key=key,
                )

            column = "mal_id" if safe_provider == "myanimelist" else "anilist_id"
            legacy_value = int(active.execute(
                f"SELECT COALESCE({column}, 0) FROM mangas WHERE id = ?",
                (safe_manga_id,),
            ).fetchone()[0] or 0)
            if (
                legacy_value > 0
                and legacy_value != int(safe_external_id)
                and safe_confidence in AUTHORITATIVE_CONFIDENCES
            ):
                key = self._record_conflict(
                    active,
                    conflict_key=f"column:{safe_manga_id}:{safe_provider}",
                    conflict_type="legacy_column_conflict",
                    provider=safe_provider,
                    external_id=safe_external_id,
                    manga_ids=[safe_manga_id],
                    details={"existing_external_id": legacy_value},
                )
                self._commit_if_owned(active, supplied)
                raise IdentityConflictError(
                    "Manga kaydındaki mevcut dış kimlik yeni kimlikle çakışıyor.",
                    conflict_key=key,
                )

            now = int(time.time())
            current = next(
                (row for row in existing_rows if row["manga_id"] == safe_manga_id),
                None,
            )
            if current and _CONFIDENCE_RANK[current["confidence"]] > _CONFIDENCE_RANK[safe_confidence]:
                safe_confidence = str(current["confidence"])
                safe_method = str(current["match_method"])
            verified_at = now if verified else int((current or {}).get("verified_at") or 0)
            active.execute(
                """
                INSERT INTO manga_external_identities (
                    manga_id, provider, external_id, confidence, match_method,
                    verified_at, last_checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(manga_id, provider, external_id) DO UPDATE SET
                    confidence = excluded.confidence,
                    match_method = excluded.match_method,
                    verified_at = MAX(manga_external_identities.verified_at, excluded.verified_at),
                    last_checked_at = excluded.last_checked_at,
                    updated_at = excluded.updated_at
                """,
                (
                    safe_manga_id, safe_provider, safe_external_id,
                    safe_confidence, safe_method, verified_at, now, now, now,
                ),
            )
            if safe_confidence in AUTHORITATIVE_CONFIDENCES and legacy_value in {0, int(safe_external_id)}:
                active.execute(
                    f"UPDATE mangas SET {column} = ? WHERE id = ?",
                    (int(safe_external_id), safe_manga_id),
                )
            self._commit_if_owned(active, supplied)
            return IdentityResolution(
                manga_id=safe_manga_id,
                confidence=safe_confidence,
                match_method=safe_method,
                candidates=(safe_manga_id,),
            )

    def link_exact_pair(
        self,
        manga_id: str,
        *,
        mal_id: Any,
        anilist_id: Any,
        match_method: str = "anilist_idmal",
        conn=None,
    ) -> IdentityResolution:
        """AniList ``idMal`` ilişkisini iki yönlü ve atomik olarak kaydeder."""
        supplied = conn
        with self._connection(conn) as active:
            if supplied is None:
                active.execute("BEGIN IMMEDIATE")
            safe_manga_id = str(manga_id or "").strip()
            safe_mal_id = self._external_id(mal_id)
            safe_anilist_id = self._external_id(anilist_id)
            if not self._manga_exists(active, safe_manga_id):
                raise ValueError("Kimliği bağlanacak manga bulunamadı")
            resolution = self.resolve(
                mal_id=safe_mal_id,
                anilist_id=safe_anilist_id,
                exact_pair=True,
                conn=active,
            )
            if resolution.conflict or (
                resolution.manga_id and resolution.manga_id != str(manga_id)
            ):
                candidates = sorted({
                    str(manga_id),
                    *resolution.candidates,
                    *([resolution.manga_id] if resolution.manga_id else []),
                })
                key = resolution.conflict_key or self._record_conflict(
                    active,
                    conflict_key=f"pair:mal:{safe_mal_id}:anilist:{safe_anilist_id}",
                    conflict_type="cross_provider_mismatch",
                    provider="myanimelist+anilist",
                    external_id=f"{safe_mal_id}:{safe_anilist_id}",
                    manga_ids=candidates,
                )
                self._commit_if_owned(active, supplied)
                raise IdentityConflictError(
                    "MAL ve AniList kimlikleri farklı MangaX kayıtlarına bağlı.",
                    conflict_key=key,
                )
            legacy = active.execute(
                "SELECT COALESCE(mal_id, 0), COALESCE(anilist_id, 0) FROM mangas WHERE id = ?",
                (safe_manga_id,),
            ).fetchone()
            target_conflicts: dict[str, list[str]] = {}
            for provider, external_id, legacy_value in (
                ("myanimelist", safe_mal_id, int(legacy[0] or 0)),
                ("anilist", safe_anilist_id, int(legacy[1] or 0)),
            ):
                other_ids = sorted({
                    str(row[0]) for row in active.execute(
                        """
                        SELECT external_id FROM manga_external_identities
                        WHERE manga_id = ? AND provider = ? AND external_id != ?
                          AND confidence IN ('exact', 'manual')
                        """,
                        (safe_manga_id, provider, external_id),
                    ).fetchall()
                })
                if legacy_value > 0 and legacy_value != int(external_id):
                    other_ids.append(str(legacy_value))
                if other_ids:
                    target_conflicts[provider] = sorted(set(other_ids))
            if target_conflicts:
                key = self._record_conflict(
                    active,
                    conflict_key=f"pair-target:{safe_manga_id}",
                    conflict_type="manga_provider_identity_conflict",
                    provider="myanimelist+anilist",
                    external_id=f"{safe_mal_id}:{safe_anilist_id}",
                    manga_ids=[safe_manga_id],
                    details={"existing_external_ids": target_conflicts},
                )
                self._commit_if_owned(active, supplied)
                raise IdentityConflictError(
                    "Manga kaydındaki mevcut dış kimlikler yeni kimlik çiftiyle çakışıyor.",
                    conflict_key=key,
                )
            self.link(
                safe_manga_id, "myanimelist", safe_mal_id,
                confidence="exact", match_method=match_method, verified=True, conn=active,
            )
            self.link(
                safe_manga_id, "anilist", safe_anilist_id,
                confidence="exact", match_method=match_method, verified=True, conn=active,
            )
            self._commit_if_owned(active, supplied)
            return IdentityResolution(
                manga_id=safe_manga_id, confidence="exact",
                match_method=match_method, candidates=(safe_manga_id,),
            )

    def external_id_for_manga(self, manga_id: str, provider: str, *, conn=None) -> int:
        safe_provider = self._provider(provider)
        with self._connection(conn) as active:
            row = active.execute(
                """
                SELECT external_id FROM manga_external_identities
                WHERE manga_id = ? AND provider = ?
                  AND confidence IN ('exact', 'manual')
                ORDER BY CASE confidence WHEN 'manual' THEN 2 ELSE 1 END DESC,
                         updated_at DESC
                LIMIT 1
                """,
                (str(manga_id), safe_provider),
            ).fetchone()
        return int(row[0]) if row else 0

    def anilist_id_for_mal(self, mal_id: Any, *, conn=None) -> int:
        resolution = self.find("myanimelist", mal_id, conn=conn)
        if not resolution.manga_id or resolution.conflict:
            return 0
        return self.external_id_for_manga(resolution.manga_id, "anilist", conn=conn)

    def mal_id_for_anilist(self, anilist_id: Any, *, conn=None) -> int:
        resolution = self.find("anilist", anilist_id, conn=conn)
        if not resolution.manga_id or resolution.conflict:
            return 0
        return self.external_id_for_manga(resolution.manga_id, "myanimelist", conn=conn)

    def list_conflicts(self, *, status: str = "open") -> list[dict[str, Any]]:
        with self.database.get_connection() as conn:
            rows = [dict(row) for row in conn.execute(
                """
                SELECT * FROM manga_identity_conflicts
                WHERE status = ? ORDER BY updated_at DESC, id DESC
                """,
                (status,),
            ).fetchall()]
        for row in rows:
            try:
                row["manga_ids"] = json.loads(row.get("manga_ids") or "[]")
                row["details"] = json.loads(row.get("details") or "{}")
            except (TypeError, ValueError):
                pass
        return rows


external_identity_service = ExternalIdentityService()
