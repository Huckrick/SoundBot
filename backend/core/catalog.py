"""SQLite catalog, keyword search, and resumable, metadata-only imports."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import islice
from pathlib import Path
from time import perf_counter
from typing import NamedTuple
from uuid import uuid4

from core.database import canonicalize_path, canonical_path_is_within

BATCH_SIZE = 256
_WRITE_LOCK = threading.RLock()
_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_SORTS = {
    "newest": ("f.id", "DESC"),
    "name": ("f.filename COLLATE NOCASE", "ASC"),
    "name_desc": ("f.filename COLLATE NOCASE", "DESC"),
    "duration": ("f.duration", "ASC"),
    "duration_desc": ("f.duration", "DESC"),
    "folder": ("soundbot_parent(f.canonical_path)", "ASC"),
}

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS library_sources (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        path TEXT NOT NULL, canonical_path TEXT NOT NULL, recursive INTEGER NOT NULL DEFAULT 1,
        UNIQUE(project_id, canonical_path))""",
    """CREATE TABLE IF NOT EXISTS source_files (
        source_id TEXT NOT NULL REFERENCES library_sources(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        scan_id TEXT NOT NULL, PRIMARY KEY(source_id, file_id))""",
    "CREATE INDEX IF NOT EXISTS idx_source_files_file ON source_files(file_id)",
    """CREATE TABLE IF NOT EXISTS library_exclusions (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        canonical_path TEXT NOT NULL, PRIMARY KEY(project_id, canonical_path))""",
    """CREATE TABLE IF NOT EXISTS library_group_files (
        folder_id TEXT NOT NULL REFERENCES user_folders(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        PRIMARY KEY(folder_id, file_id))""",
    """CREATE TABLE IF NOT EXISTS catalog_imports (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        source_id TEXT REFERENCES library_sources(id) ON DELETE CASCADE,
        files_json TEXT NOT NULL DEFAULT '[]', discovered INTEGER NOT NULL DEFAULT 0,
        imported INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0, incomplete INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS import_directories (
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        path TEXT NOT NULL, UNIQUE(job_id, path))""",
    "CREATE INDEX IF NOT EXISTS idx_import_directories_job ON import_directories(job_id, id)",
    """CREATE TABLE IF NOT EXISTS import_failures (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        path TEXT NOT NULL, message TEXT NOT NULL, PRIMARY KEY(job_id, path))""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS library_search USING fts5(
        name, description, keywords, tags, ucs, library, path, prefix='2 3')""",
)

_COLUMNS = {
    "title": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "user_title": "TEXT",
    "user_description": "TEXT",
    "keywords": "TEXT NOT NULL DEFAULT ''",
    "library_name": "TEXT NOT NULL DEFAULT ''",
    "ucs": "TEXT NOT NULL DEFAULT ''",
    "format": "TEXT NOT NULL DEFAULT ''",
    "mtime_ns": "INTEGER",
    "favorite": "INTEGER NOT NULL DEFAULT 0",
    "missing": "INTEGER NOT NULL DEFAULT 0",
    "probe_error": "TEXT",
}

_SEARCH_COLUMNS = "rowid, name, description, keywords, tags, ucs, library, path"
_SEARCH_VALUES = (
    "r.id, soundbot_terms(r.filename || ' ' || COALESCE(r.user_title, r.title)), "
    "soundbot_terms(COALESCE(r.user_description, r.description)), "
    "soundbot_terms(r.keywords), soundbot_terms(r.tags), soundbot_terms(r.ucs), "
    "soundbot_terms(r.library_name), soundbot_terms(r.path)"
)


def search_terms(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    words = _WORDS.findall(text)
    for segment in _CJK.findall(text):
        words.extend(segment)
        words.extend(segment[i:i + 2] for i in range(len(segment) - 1))
    return " ".join(dict.fromkeys(words))


def configure_connection(connection):
    connection.create_function("soundbot_terms", 1, search_terms, deterministic=True)
    connection.create_function("soundbot_extension", 1, lambda value: Path(value).suffix[1:].casefold(), deterministic=True)
    connection.create_function("soundbot_parent", 1, lambda value: value.rsplit("/", 1)[0] or "/", deterministic=True)


def migrate(cursor):
    for name, definition in _COLUMNS.items():
        cursor.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
    cursor.execute("UPDATE files SET format=soundbot_extension(filename)")
    for statement in _SCHEMA:
        cursor.execute(statement)
    cursor.execute("CREATE INDEX idx_files_library_name ON files(project_id, filename COLLATE NOCASE, id)")
    cursor.execute("CREATE INDEX idx_files_library_duration ON files(project_id, duration, id)")
    cursor.execute("CREATE INDEX idx_files_library_parent ON files(project_id, soundbot_parent(canonical_path), id)")
    cursor.execute("CREATE INDEX idx_files_library_ucs ON files(project_id, ucs)")
    cursor.execute("CREATE INDEX idx_files_library_favorite ON files(project_id, favorite, id)")
    fields = "title, description, user_title, user_description, keywords, ucs, library_name"
    values = _SEARCH_VALUES.replace("r.", "new.")
    cursor.execute(f"""CREATE TRIGGER library_search_insert AFTER INSERT ON files BEGIN
        INSERT INTO library_search({_SEARCH_COLUMNS}) VALUES ({values});
        END""")
    cursor.execute(f"""CREATE TRIGGER library_search_update
        AFTER UPDATE OF filename, path, tags, {fields} ON files
        WHEN old.filename IS NOT new.filename OR old.path IS NOT new.path
          OR old.tags IS NOT new.tags OR old.title IS NOT new.title
          OR old.description IS NOT new.description OR old.keywords IS NOT new.keywords
          OR old.user_title IS NOT new.user_title OR old.user_description IS NOT new.user_description
          OR old.ucs IS NOT new.ucs OR old.library_name IS NOT new.library_name
        BEGIN
        DELETE FROM library_search WHERE rowid=old.id;
        INSERT INTO library_search({_SEARCH_COLUMNS}) VALUES ({values});
        END""")
    cursor.execute("""CREATE TRIGGER library_search_delete AFTER DELETE ON files BEGIN
        DELETE FROM library_search WHERE rowid=old.id;
        END""")
    rebuild_keywords(cursor)
    for mapping in cursor.execute("SELECT * FROM imported_folder_mappings").fetchall():
        key = canonicalize_path(mapping["folder_path"])
        if not key:
            continue
        cursor.execute(
            """INSERT OR IGNORE INTO library_sources(id, project_id, path, canonical_path)
            VALUES (?, ?, ?, ?)""",
            (uuid4().hex, mapping["project_id"], mapping["folder_path"], key),
        )
        source = cursor.execute(
            "SELECT id FROM library_sources WHERE project_id=? AND canonical_path=?",
            (mapping["project_id"], key),
        ).fetchone()
        lo, hi = path_bounds(key)
        cursor.execute(
            """INSERT OR IGNORE INTO source_files SELECT ?, id, '' FROM files
            WHERE project_id=? AND canonical_path>=? AND canonical_path<?""",
            (source["id"], mapping["project_id"], lo, hi),
        )
        if mapping["user_folder_id"]:
            cursor.execute(
                """INSERT OR IGNORE INTO library_group_files
                SELECT ?, file_id FROM source_files WHERE source_id=?""",
                (mapping["user_folder_id"], source["id"]),
            )


def rebuild_keywords(cursor):
    cursor.execute("DELETE FROM library_search")
    cursor.execute(f"INSERT INTO library_search({_SEARCH_COLUMNS}) SELECT {_SEARCH_VALUES} FROM files r")


def path_bounds(path):
    prefix = path.rstrip("/") + "/"
    return prefix, prefix[:-1] + "0"


@lru_cache(maxsize=1)
def _ucs_processor():
    from core.ucs_keywords import UCSKeywordProcessor
    return UCSKeywordProcessor()


def _search_expression(query):

    def expression(text):
        parts = []
        for word in _WORDS.findall(unicodedata.normalize("NFKC", text).casefold()):
            if _CJK.fullmatch(word):
                terms = [word] if len(word) <= 2 else [word[i:i + 2] for i in range(len(word) - 1)]
                parts.extend(f'"{term}"' for term in terms)
            else:
                parts.append(f'"{word}"*')
        return " AND ".join(parts)

    expanded = _ucs_processor().expand_query_with_tokenization(query)
    expressions = dict.fromkeys(filter(None, (expression(value) for value in [query, *expanded[:12]])))
    return " OR ".join(f"({item})" for item in expressions)


def list_items(db, project_id, *, query="", folder=None, group=None, tag=None, ucs=None,
               favorite=False, audio_format=None, channels=None, sample_rate=None,
               min_duration=None, max_duration=None, sort="newest", limit=100, cursor=None,
               offset=0, count=False, file_id=None):
    expressions = ["f.project_id=?"]
    params = [project_id]
    if file_id:
        expressions.append("f.file_uuid=?")
        params.append(file_id)
    match = _search_expression(query[:300]) if query.strip() else ""
    if query.strip() and not match:
        return {"files": [], "next_cursor": None, "total": 0}
    join = "JOIN library_search ON library_search.rowid=f.id" if match else ""
    if match:
        expressions.append("library_search MATCH ?")
        params.append(match)
    if folder:
        expressions.append("f.canonical_path>=? AND f.canonical_path<?")
        params.extend(path_bounds(canonicalize_path(folder)))
    if group:
        expressions.append("EXISTS (SELECT 1 FROM library_group_files g WHERE g.file_id=f.id AND g.folder_id=?)")
        params.append(group)
    if tag:
        expressions.append("EXISTS (SELECT 1 FROM json_each(f.tags) WHERE value=?)")
        params.append(tag)
    if favorite:
        expressions.append("f.favorite=1")
    for field, operator, value in (
        ("ucs", "=", ucs), ("format", "=", audio_format), ("channels", "=", channels),
        ("sample_rate", "=", sample_rate), ("duration", ">=", min_duration),
        ("duration", "<=", max_duration),
    ):
        if value is not None:
            expressions.append(f"f.{field}{operator}?")
            params.append(value)
    score = "bm25(library_search, 10, 3, 6, 8, 9, 3, 1)" if match else "0.0"
    field, direction = ("rank", "ASC") if match else _SORTS.get(sort, _SORTS["newest"])
    where = " AND ".join(expressions)
    with db.get_cursor() as cur:
        total = cur.execute(
            f"SELECT COUNT(*) FROM files f {join} WHERE {where}", params
        ).fetchone()[0] if count else None
        select = f"""SELECT f.id AS row_id, f.file_uuid AS id, f.filename, f.path, f.format,
            COALESCE(f.user_title,f.title) AS title, COALESCE(f.user_description,f.description) AS description,
            f.keywords, f.library_name, f.ucs, f.sample_rate, f.channels,
            f.duration, f.file_size AS size, f.tags, f.favorite, f.missing, f.probe_error,
            f.created_at, f.source_fingerprint, f.waveform_fingerprint,
            soundbot_parent(f.canonical_path) AS folder_key, {score} AS rank
            FROM files f {join} WHERE {where}"""
        if cursor:
            compare = ">" if direction == "ASC" else "<"
            value, row_id = cursor
            if match:
                # SQLite FTS5 restricts bm25() to the MATCH query context.
                select = f"SELECT * FROM ({select}) AS page WHERE (rank, row_id) > (?, ?)"
                params.extend((value, row_id))
                field = "rank"
            else:
                select += f" AND ({field}, f.id) {compare} (?, ?)"
                params.extend((value, row_id))
        order = f"{field} {direction}, row_id {direction}"
        rows = cur.execute(f"{select} ORDER BY {order} LIMIT ? OFFSET ?",
                           (*params, limit + 1, offset)).fetchall()
    items = []
    for row in rows[:limit]:
        item = dict(row)
        item["tags"] = json.loads(item["tags"] or "[]")
        item["format"] = item["format"] or Path(item["path"]).suffix[1:].lower()
        item["favorite"] = bool(item["favorite"])
        item["missing"] = bool(item["missing"])
        items.append(item)
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        column = "rank" if match else {"name": "filename", "duration": "duration", "folder": "folder_key"}.get(
            sort.removesuffix("_desc"), "row_id")
        next_cursor = [last[column], last["row_id"]]
    return {"files": items, "next_cursor": next_cursor, "total": total}


class Candidate(NamedTuple):
    path: str
    key: str
    size: int
    mtime: int


def _candidate(path, stat):
    return Candidate(str(path), canonicalize_path(str(path)), stat.st_size, stat.st_mtime_ns)


def _probe(item):
    from core.audio_service import get_audio_service, AudioServiceError
    from core.ucs_keywords import category_codes

    try:
        info = get_audio_service().probe(item.path)
        tags = {str(key).lower(): str(value)[:4096] for key, value in info.tags.items()}
        ucs = tags.get("catid") or tags.get("cat_id") or tags.get("ucs") or ""
        if not ucs:
            prefix = Path(item.path).stem.split("_", 1)[0]
            ucs = category_codes().get(prefix.casefold(), "")
        return item, (
            info.duration, info.sample_rate, info.channels, info.format,
            tags.get("title", ""), tags.get("description") or tags.get("comment", ""),
            tags.get("keywords") or tags.get("keyword", ""), tags.get("library") or tags.get("library_name", ""), ucs, None,
        )
    except (OSError, AudioServiceError) as exc:
        reason = exc.details.get("reason") if isinstance(exc, AudioServiceError) else None
        error = f"{exc} · {reason}" if reason else str(exc)
        return item, (0, 0, 0, Path(item.path).suffix[1:].lower(), "", "", "", "", "", error)


def create_import(db, project_id, *, root=None, paths=(), recursive=True, source_id=None):
    job_id = uuid4().hex
    with _WRITE_LOCK, db.get_cursor() as cur:
        if root:
            key = canonicalize_path(root)
            cur.execute(
                """INSERT INTO library_sources(id, project_id, path, canonical_path, recursive)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, canonical_path)
                DO UPDATE SET path=excluded.path, recursive=excluded.recursive""",
                (uuid4().hex, project_id, root, key, int(recursive)),
            )
            source_id = cur.execute(
                "SELECT id FROM library_sources WHERE project_id=? AND canonical_path=?", (project_id, key)
            ).fetchone()[0]
        if source_id:
            source = cur.execute("SELECT path FROM library_sources WHERE project_id=? AND id=?",
                                 (project_id, source_id)).fetchone()
            if not source:
                raise ValueError("来源目录不存在")
            root = source["path"]
        cur.execute("INSERT INTO jobs(id,project_id,kind,stage) VALUES (?,?,'catalog_import','queued')",
                    (job_id, project_id))
        cur.execute("INSERT INTO catalog_imports(job_id,source_id,files_json) VALUES (?,?,?)",
                    (job_id, source_id, json.dumps(list(paths))))
        if root:
            cur.execute("INSERT INTO import_directories(job_id,path) VALUES (?,?)", (job_id, root))
    return job_id


def import_status(db, job_id):
    job = db.get_job(job_id)
    if job:
        with db.get_cursor() as cur:
            details = cur.execute("SELECT * FROM catalog_imports WHERE job_id=?", (job_id,)).fetchone()
            if details:
                job.update({key: details[key] for key in ("discovered", "imported", "unchanged", "failures")})
                job["source_id"] = details["source_id"]
                job["failed_items"] = [dict(row) for row in cur.execute(
                    "SELECT path, message FROM import_failures WHERE job_id=? ORDER BY path LIMIT 100", (job_id,)
                )]
    return job


def retry_import(db, job_id, project_id):
    with db.get_cursor() as cur:
        task = cur.execute("SELECT source_id FROM catalog_imports WHERE job_id=?", (job_id,)).fetchone()
        paths = [row[0] for row in cur.execute("SELECT path FROM import_failures WHERE job_id=?", (job_id,))]
    if not task or not paths:
        raise ValueError("任务没有可重试的项目")
    return create_import(db, project_id, source_id=task["source_id"], paths=() if task["source_id"] else paths)


def _write_failure(cur, job_id, path, message):
    cur.execute("""INSERT INTO import_failures(job_id,path,message) VALUES (?,?,?)
        ON CONFLICT(job_id,path) DO UPDATE SET message=excluded.message""",
        (job_id, path, message))


def _commit_batch(db, job_id, project_id, source_id, items, pool, explicit, path_failures=(), *, timings=None):
    from core.audio_service import FileFingerprint

    started = perf_counter()
    keys = [item.key for item in items]
    slots = ",".join("?" for _ in keys)
    with db.get_cursor() as cur:
        known = {row["canonical_path"]: row for row in cur.execute(
            f"""SELECT f.id,f.canonical_path,f.mtime_ns,f.file_size,f.probe_error,s.scan_id
            FROM files f LEFT JOIN source_files s ON s.file_id=f.id AND s.source_id=?
            WHERE f.project_id=? AND f.canonical_path IN ({slots})""", (source_id, project_id, *keys))}
        excluded = set() if explicit else {row[0] for row in cur.execute(
            f"SELECT canonical_path FROM library_exclusions WHERE project_id=? AND canonical_path IN ({slots})",
            (project_id, *keys))}
    unchanged = {}
    pending = {}
    discovered = len(path_failures)
    for item in {item.key: item for item in items}.values():
        if item.key in excluded:
            continue
        prior = known.get(item.key)
        if prior and prior["scan_id"] == job_id:
            continue
        discovered += 1
        if prior and prior["mtime_ns"] == item.mtime and prior["file_size"] == item.size and not prior["probe_error"]:
            unchanged[item.key] = prior["id"]
        else:
            pending[item.key] = item
    probe_started = perf_counter()
    results = list(pool.map(_probe, pending.values()))
    commit_started = perf_counter()
    with _WRITE_LOCK, db.get_cursor() as cur:
        for path, message in path_failures:
            _write_failure(cur, job_id, path, message)
        if path_failures and source_id:
            cur.execute("UPDATE catalog_imports SET incomplete=1 WHERE job_id=?", (job_id,))
        failures = len(path_failures)
        unchanged_count = len(unchanged)
        inserted = 0
        for item, fields in results:
            # A user may have excluded the record while the probe ran.
            if not explicit and cur.execute(
                "SELECT 1 FROM library_exclusions WHERE project_id=? AND canonical_path=?",
                (project_id, item.key),
            ).fetchone():
                continue
            fingerprint = FileFingerprint(item.size, item.mtime).source_key
            file_id = cur.execute("""
                INSERT INTO files(file_uuid,project_id,path,canonical_path,filename,file_size,mtime_ns,
                    source_fingerprint,duration,sample_rate,channels,format,title,description,keywords,library_name,ucs,probe_error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,canonical_path) DO UPDATE SET
                    path=excluded.path, filename=excluded.filename, file_size=excluded.file_size,
                    mtime_ns=excluded.mtime_ns, source_fingerprint=excluded.source_fingerprint,
                    duration=excluded.duration, sample_rate=excluded.sample_rate, channels=excluded.channels,
                    format=excluded.format, title=excluded.title, description=excluded.description,
                    keywords=excluded.keywords, library_name=excluded.library_name, ucs=excluded.ucs,
                    probe_error=excluded.probe_error, missing=0, updated_at=CURRENT_TIMESTAMP,
                    peaks_json=CASE WHEN files.source_fingerprint=excluded.source_fingerprint THEN files.peaks_json END,
                    waveform_fingerprint=CASE WHEN files.source_fingerprint=excluded.source_fingerprint
                        THEN files.waveform_fingerprint END
                RETURNING id,file_uuid
                """, (uuid4().hex, project_id, item.path, item.key,
                      Path(item.path).name, item.size, item.mtime, fingerprint, *fields)).fetchone()
            if item.key in known:
                cur.execute("""UPDATE file_artifacts SET state='stale',source_fingerprint=?
                    WHERE file_uuid=? AND source_fingerprint IS NOT ?""",
                    (fingerprint, file_id["file_uuid"], fingerprint))
            unchanged[item.key] = file_id["id"]
            inserted += 1
            if fields[-1]:
                failures += 1
                _write_failure(cur, job_id, item.path, fields[-1])
            else:
                cur.execute("DELETE FROM import_failures WHERE job_id=? AND path=?", (job_id, item.path))
        for key, file_id in unchanged.items():
            if explicit:
                cur.execute("DELETE FROM library_exclusions WHERE project_id=? AND canonical_path=?", (project_id, key))
            if source_id:
                cur.execute("""INSERT INTO source_files(source_id,file_id,scan_id)
                    SELECT ?,id,? FROM files WHERE id=? ON CONFLICT(source_id,file_id)
                    DO UPDATE SET scan_id=excluded.scan_id""", (source_id, job_id, file_id))
            cur.execute("UPDATE files SET missing=0 WHERE id=? AND missing=1", (file_id,))
        processed = len(items) + len(path_failures) if explicit else discovered
        cur.execute("""UPDATE catalog_imports SET discovered=discovered+?,imported=imported+?,
            unchanged=unchanged+?,failures=failures+? WHERE job_id=?""",
            (processed, inserted, unchanged_count, failures, job_id))
        cur.execute("""UPDATE jobs SET total=total+?,processed=processed+?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""", (processed, processed, job_id))
    if timings is not None:
        timings["lookup"] += probe_started - started
        timings["probe"] += commit_started - probe_started
        timings["write"] += perf_counter() - commit_started


def _scan_directory(db, job_id, project_id, source, directory, pool, timings=None):
    import config

    def flush(batch, directories, failures):
        if batch or failures:
            _commit_batch(db, job_id, project_id, source["id"], batch, pool, False, failures, timings=timings)
            batch.clear()
            failures.clear()
        if directories:
            with _WRITE_LOCK, db.get_cursor() as cur:
                cur.executemany("INSERT OR IGNORE INTO import_directories(job_id,path) VALUES (?,?)",
                                ((job_id, path) for path in directories))
            directories.clear()

    batch, directories, failures = [], [], []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index % BATCH_SIZE == 0 and _stopping(db, job_id):
                return False
            if entry.name.startswith("._"):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if source["recursive"]:
                        directories.append(entry.path)
                elif Path(entry.name).suffix.lower() in config.AUDIO_FORMAT_CAPABILITIES and entry.is_file():
                    batch.append(_candidate(entry.path, entry.stat()))
            except OSError as exc:
                failures.append((entry.path, str(exc)))
            if len(batch) + len(failures) >= BATCH_SIZE or len(directories) >= BATCH_SIZE:
                flush(batch, directories, failures)
        flush(batch, directories, failures)
    return True


def _stopping(db, job_id):
    job = db.get_job(job_id)
    return not job or job["cancel_requested"] or job["state"] != "running"


def run_import(db, job_id, *, timings=None):
    with _WRITE_LOCK, db.get_cursor() as cur:
        claimed = cur.execute("""UPDATE jobs SET state='running',stage='importing',cancel_requested=0
            WHERE id=? AND state='pending' AND cancel_requested=0 RETURNING project_id""", (job_id,)).fetchone()
        if not claimed:
            return
        task = cur.execute("SELECT * FROM catalog_imports WHERE job_id=?", (job_id,)).fetchone()
        source = cur.execute("SELECT * FROM library_sources WHERE id=?", (task["source_id"],)).fetchone()
    project_id = claimed["project_id"]
    try:
        from core.ucs_keywords import category_codes

        category_codes()
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="soundbot-probe") as pool:
            if source:
                while not _stopping(db, job_id):
                    with db.get_cursor() as cur:
                        directory = cur.execute("""SELECT id,path FROM import_directories
                            WHERE job_id=? ORDER BY id LIMIT 1""", (job_id,)).fetchone()
                    if directory is None:
                        break
                    try:
                        if not _scan_directory(db, job_id, project_id, source, directory["path"], pool, timings):
                            break
                    except OSError as exc:
                        with _WRITE_LOCK, db.get_cursor() as cur:
                            _write_failure(cur, job_id, directory["path"], str(exc))
                            cur.execute("""UPDATE catalog_imports SET failures=failures+1,incomplete=1
                                WHERE job_id=?""", (job_id,))
                    with _WRITE_LOCK, db.get_cursor() as cur:
                        cur.execute("DELETE FROM import_directories WHERE id=?", (directory["id"],))
            else:
                paths = iter(json.loads(task["files_json"])[task["discovered"]:])
                while not _stopping(db, job_id) and (chunk := list(islice(paths, BATCH_SIZE))):
                    items, failed = [], []
                    for path in chunk:
                        try:
                            stat = os.stat(path)
                            if not Path(path).is_file():
                                raise ValueError("路径不是音频文件")
                            items.append(_candidate(path, stat))
                        except (OSError, ValueError) as exc:
                            failed.append((path, str(exc)))
                    _commit_batch(db, job_id, project_id, None, items, pool, True, failed, timings=timings)
        with _WRITE_LOCK, db.get_cursor() as cur:
            job = cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return
            if job["cancel_requested"] or job["state"] != "running":
                cur.execute("UPDATE jobs SET state='cancelled', stage='paused' WHERE id=?", (job_id,))
                return
            progress = cur.execute("SELECT incomplete,failures FROM catalog_imports WHERE job_id=?", (job_id,)).fetchone()
            incomplete = progress["incomplete"]
            if source and not incomplete:
                cur.execute("""UPDATE files SET missing=1 WHERE id IN (
                    SELECT file_id FROM source_files WHERE source_id=? AND scan_id<>?
                    ) AND canonical_path>=? AND canonical_path<?
                    AND (? OR instr(substr(canonical_path,?),'/')=0)""",
                    (source["id"], job_id, *path_bounds(source["canonical_path"]),
                     source["recursive"], len(path_bounds(source["canonical_path"])[0]) + 1))
            cur.execute("UPDATE jobs SET state='completed',stage=? WHERE id=?",
                        ("partial" if incomplete or progress["failures"] else "done", job_id))
    except Exception as exc:
        db.update_job(job_id, state="failed", stage="interrupted", error_message=str(exc),
                      error_code="catalog_import_failed")


def sources(db, project_id):
    with db.get_cursor() as cur:
        return [dict(row) for row in cur.execute("""SELECT s.id,s.path,s.recursive,COUNT(f.file_id) AS count
            FROM library_sources s LEFT JOIN source_files f ON f.source_id=s.id
            WHERE s.project_id=? GROUP BY s.id ORDER BY s.path COLLATE NOCASE""", (project_id,))]


def facets(db, project_id):
    with db.get_cursor() as cur:
        tags = [dict(row) for row in cur.execute("""SELECT t.value AS name,COUNT(*) AS count
            FROM files f,json_each(f.tags) t WHERE f.project_id=?
            GROUP BY t.value ORDER BY count DESC,name LIMIT 1000""", (project_id,))]
        ucs = [dict(row) for row in cur.execute("""SELECT ucs AS name,COUNT(*) AS count
            FROM files WHERE project_id=? AND ucs<>'' GROUP BY ucs ORDER BY ucs""", (project_id,))]
    return {"tags": tags, "ucs": ucs, "groups": db.get_user_folders(project_id)}


def asset_groups(db, project_id, file_id):
    with db.get_cursor() as cur:
        return [dict(row) for row in cur.execute("""
            SELECT uf.id, uf.name, EXISTS (
                SELECT 1 FROM library_group_files gf JOIN files f ON f.id=gf.file_id
                WHERE gf.folder_id=uf.id AND f.project_id=uf.project_id AND f.file_uuid=?
            ) AS member
            FROM user_folders uf WHERE uf.project_id=?
            ORDER BY uf.sort_order, uf.created_at
            """, (file_id, project_id))]


def folders(db, project_id, folder=None):
    with db.get_cursor() as cur:
        if folder is None:
            roots = sources(db, project_id)
            registered = {canonicalize_path(root["path"]) for root in roots}
            loose = cur.execute("""SELECT soundbot_parent(canonical_path) AS path, COUNT(*) AS count
                FROM files f WHERE project_id=? AND NOT EXISTS (
                    SELECT 1 FROM source_files sf WHERE sf.file_id=f.id)
                GROUP BY soundbot_parent(canonical_path) ORDER BY 1""", (project_id,))
            roots.extend(dict(row) for row in loose if row["path"] not in registered)
            return roots
        key = canonicalize_path(folder)
        lo, hi = path_bounds(key)
        return [dict(row) for row in cur.execute("""
            WITH paths AS (
                SELECT SUBSTR(canonical_path, ?) AS relative FROM files
                WHERE project_id=? AND canonical_path>=? AND canonical_path<?
            )
            SELECT ? || SUBSTR(relative,1,INSTR(relative,'/')-1) AS path, COUNT(*) AS count
            FROM paths WHERE INSTR(relative,'/')>0 GROUP BY path ORDER BY path COLLATE NOCASE
            """, (len(lo) + 1, project_id, lo, hi, lo))]


def mutate_items(db, project_id, ids, action, value):
    if not ids:
        return 0
    slots = ",".join("?" for _ in ids)
    with _WRITE_LOCK, db.get_cursor() as cur:
        params = (project_id, *ids)
        where = f"project_id=? AND file_uuid IN ({slots})"
        if action == "remove":
            cur.execute(f"""INSERT OR IGNORE INTO library_exclusions(project_id,canonical_path)
                SELECT project_id,canonical_path FROM files WHERE {where}""", params)
            cur.execute(f"DELETE FROM files WHERE {where}", params)
        elif action == "favorite":
            cur.execute(f"UPDATE files SET favorite=? WHERE {where}", (int(bool(value)), *params))
        elif action in ("group", "ungroup"):
            if not isinstance(value, str) or not cur.execute(
                "SELECT 1 FROM user_folders WHERE id=? AND project_id=?", (value, project_id)
            ).fetchone():
                raise ValueError("分组不存在")
            if action == "group":
                cur.execute(f"""INSERT OR IGNORE INTO library_group_files(folder_id,file_id)
                    SELECT ?,id FROM files WHERE {where}""", (value, *params))
            else:
                cur.execute(f"""DELETE FROM library_group_files WHERE folder_id=?
                    AND file_id IN (SELECT id FROM files WHERE {where})""", (value, *params))
        elif action == "tags":
            rows = cur.execute(f"SELECT id,tags FROM files WHERE {where}", params).fetchall()
            cur.executemany("UPDATE files SET tags=? WHERE id=?", (
                (json.dumps(list(dict.fromkeys([*json.loads(row["tags"] or "[]"), *value])),
                            ensure_ascii=False), row["id"]) for row in rows
            ))
        elif action == "metadata":
            tags = value.get("tags")
            tag_json = json.dumps(list(dict.fromkeys(tags)), ensure_ascii=False) if tags is not None else None
            cur.execute(f"UPDATE files SET user_title=?,user_description=?,tags=COALESCE(?,tags) WHERE {where}",
                        (value["title"], value["description"], tag_json, *params))
        else:
            raise ValueError("未知操作")
        return cur.rowcount


def relocate(db, project_id, destination, *, file_id=None, source_id=None):
    from core.audio_service import FileFingerprint

    if file_id:
        target = Path(destination).resolve(strict=True)
        if not target.is_file():
            raise ValueError("目标文件不可用")
        candidate = _candidate(target, target.stat())
        item, metadata = _probe(candidate)
        if metadata[-1]:
            raise ValueError(metadata[-1])
        fingerprint = FileFingerprint(item.size, item.mtime).source_key
        with _WRITE_LOCK, db.get_cursor() as cur:
            row = cur.execute("SELECT id,canonical_path FROM files WHERE file_uuid=? AND project_id=?",
                              (file_id, project_id)).fetchone()
            if not row:
                raise ValueError("素材不存在")
            cur.execute("""UPDATE files SET path=?,canonical_path=?,filename=?,missing=0,
                mtime_ns=?,file_size=?,source_fingerprint=?,
                duration=?,sample_rate=?,channels=?,format=?,title=?,description=?,keywords=?,
                library_name=?,ucs=?,probe_error=?,
                peaks_json=CASE WHEN source_fingerprint IS ? THEN peaks_json END,
                waveform_fingerprint=CASE WHEN source_fingerprint IS ? THEN waveform_fingerprint END
                WHERE id=?""", (item.path, item.key, target.name, item.mtime, item.size, fingerprint,
                                *metadata, fingerprint, fingerprint, row["id"]))
            cur.execute("""UPDATE file_artifacts SET state='stale',source_fingerprint=?
                WHERE file_uuid=? AND source_fingerprint IS NOT ?""", (fingerprint, file_id, fingerprint))
            if row["canonical_path"] != item.key:
                cur.execute("""DELETE FROM source_files WHERE file_id=? AND source_id IN (
                    SELECT id FROM library_sources WHERE substr(?,1,length(rtrim(canonical_path,'/'))+1) <> rtrim(canonical_path,'/') || '/')""",
                    (row["id"], item.key))
            return 1

    target = Path(destination).resolve(strict=True)
    if not target.is_dir():
        raise ValueError("目标目录不可用")
    with _WRITE_LOCK, db.get_cursor() as cur:
        source = cur.execute("SELECT * FROM library_sources WHERE id=? AND project_id=?",
                             (source_id, project_id)).fetchone()
        if not source:
            raise ValueError("来源目录不可用")
        old = source["canonical_path"]
        new = canonicalize_path(str(target))
        if old == new:
            return 0
        if canonical_path_is_within(new, old) or canonical_path_is_within(old, new):
            raise ValueError("请选择与原目录不重叠的新位置")
        lo, hi = path_bounds(old)
        prefix = new.rstrip("/") + "/"
        changed = cur.execute("""UPDATE files SET
            path=? || SUBSTR(canonical_path,?),canonical_path=? || SUBSTR(canonical_path,?)
            WHERE project_id=? AND canonical_path>=? AND canonical_path<?""",
            (prefix, len(lo)+1, prefix, len(lo)+1, project_id, lo, hi)).rowcount
        cur.execute("""UPDATE library_exclusions SET canonical_path=? || SUBSTR(canonical_path,?)
            WHERE project_id=? AND canonical_path>=? AND canonical_path<?""",
            (prefix, len(lo)+1, project_id, lo, hi))
        cur.execute("""UPDATE library_sources SET path=? || SUBSTR(canonical_path,?),
            canonical_path=? || SUBSTR(canonical_path,?)
            WHERE project_id=? AND canonical_path>=? AND canonical_path<?""",
            (prefix, len(lo)+1, prefix, len(lo)+1, project_id, lo, hi))
        cur.execute("UPDATE library_sources SET path=?,canonical_path=? WHERE id=?", (str(target), new, source_id))
        cur.execute("""UPDATE import_directories SET path=? || SUBSTR(path,?)
            WHERE (path=? OR substr(path,1,?)=?) AND job_id IN (
              SELECT t.job_id FROM catalog_imports t JOIN jobs j ON j.id=t.job_id
              WHERE t.source_id IS NOT NULL AND j.project_id=? AND j.state<>'completed'
            )""", (str(target), len(source["path"])+1, source["path"],
                   len(source["path"].rstrip("/")+"/"), source["path"].rstrip("/")+"/", project_id))
        return changed


def list_jobs(db, project_id):
    with db.get_cursor() as cur:
        ids = [row[0] for row in cur.execute(
            "SELECT id FROM jobs WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT 30",
            (project_id,),
        )]
    return [import_status(db, job_id) for job_id in ids]


def active_jobs(db):
    with db.get_cursor() as cur:
        return [dict(row) for row in cur.execute("""
            SELECT j.id, j.project_id, j.kind, j.state, j.processed, p.name AS library_name,
                   i.discovered, i.imported, i.unchanged, i.failures,
                   CASE WHEN i.source_id IS NULL THEN json_array_length(i.files_json) END AS expected
            FROM jobs j JOIN projects p ON p.id=j.project_id
            LEFT JOIN catalog_imports i ON i.job_id=j.id
            WHERE j.state IN ('pending', 'running')
            ORDER BY j.created_at, j.rowid
        """)]


def rebuild_job(db, job_id):
    try:
        with _WRITE_LOCK, db.get_cursor() as cur:
            changed = cur.execute("UPDATE jobs SET state='running',stage='indexing' "
                                  "WHERE id=? AND state='pending'", (job_id,)).rowcount
            if changed:
                rebuild_keywords(cur)
                cur.execute("UPDATE jobs SET state='completed',stage='done' WHERE id=?", (job_id,))
    except sqlite3.Error as exc:
        db.update_job(job_id, state="failed", stage="indexing", error_message=str(exc))
