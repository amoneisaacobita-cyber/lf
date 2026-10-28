#!/usr/bin/env python3
"""Life Fountain Church web server. Python standard library only."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LFC_DATA_DIR", ROOT / "data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
DATABASE = DATA_DIR / "life-fountain.sqlite3"
MAX_JSON_BYTES = 64 * 1024
MAX_AUDIO_CHUNK_BYTES = 2 * 1024 * 1024
MAX_MEDIA_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SERMON_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
SERMON_UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
SERMON_UPLOAD_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_RECORDING_BYTES = 500 * 1024 * 1024
MEDIA_DISCOVERY_STABILITY_SECONDS = 2
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000
UUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SERMON_UPLOAD_LOCKS: dict[str, threading.Lock] = {}
SERMON_UPLOAD_LOCKS_GUARD = threading.Lock()
DISCOVERABLE_AUDIO_EXTENSIONS = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def discovered_audio_is_complete(path: Path, size: int) -> bool:
    if size < 128:
        return False
    with path.open("rb") as audio_file:
        header = audio_file.read(12)
    extension = path.suffix.lower()
    if extension == ".wav":
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return False
        riff_size = int.from_bytes(header[4:8], "little")
        return riff_size >= 36 and riff_size + 8 == size
    if extension == ".mp3":
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        )
    if extension == ".m4a":
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if extension in (".ogg", ".oga"):
        return header.startswith(b"OggS")
    if extension == ".webm":
        return header.startswith(b"\x1aE\xdf\xa3")
    if extension == ".flac":
        return header.startswith(b"fLaC")
    if extension == ".aac":
        return len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0
    return False


def discover_media_cover(audio_path: Path) -> Path | None:
    cover_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    candidates = [
        *audio_path.parent.glob(f"{audio_path.stem}.*"),
        *(audio_path.parent / f"cover{extension}" for extension in cover_extensions),
        *(audio_path.parent / f"wallpaper{extension}" for extension in cover_extensions),
        *(audio_path.parent / f"background{extension}" for extension in cover_extensions),
    ]
    for candidate in candidates:
        if (
            candidate.suffix.lower() not in cover_extensions
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            continue
        try:
            with candidate.open("rb") as image_file:
                header = image_file.read(12)
            is_valid = (
                candidate.suffix.lower() in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff")
            ) or (
                candidate.suffix.lower() == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
            ) or (
                candidate.suffix.lower() == ".webp"
                and header.startswith(b"RIFF")
                and header[8:12] == b"WEBP"
            )
            if is_valid and candidate.stat().st_size <= 5 * 1024 * 1024:
                return candidate
        except OSError as exc:
            print(f"Could not inspect sermon wallpaper {candidate}: {exc}")
    return None


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(DATABASE, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                role TEXT NOT NULL CHECK (role IN ('admin', 'pastor')),
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studio_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studio_sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_ip TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prayers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                pastor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                client_ip TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                response TEXT NOT NULL DEFAULT '',
                responded_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                responded_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS church_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL,
                leader TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                about TEXT NOT NULL DEFAULT '',
                latitude REAL,
                longitude REAL,
                logo_path TEXT,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                audience TEXT NOT NULL CHECK (audience IN ('everyone', 'pastors')),
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS church_verses (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                reference TEXT NOT NULL,
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS streams (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                cover_path TEXT,
                owner_id TEXT NOT NULL REFERENCES users(id),
                started_at INTEGER NOT NULL,
                ended_at INTEGER,
                status TEXT NOT NULL CHECK (status IN ('live', 'completed', 'interrupted'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_stream
                ON streams(status) WHERE status = 'live';
            CREATE TABLE IF NOT EXISTS audio_chunks (
                stream_id TEXT NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                PRIMARY KEY (stream_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS media_items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('sermon', 'short')),
                mime_type TEXT NOT NULL,
                original_path TEXT NOT NULL,
                audio_path TEXT,
                cover_path TEXT,
                view_count INTEGER NOT NULL DEFAULT 0,
                auto_discovered INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS media_discovery_candidates (
                file_path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                first_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sermon_uploads (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                original_filename TEXT NOT NULL,
                title TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                cover_path TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS prayer_assignment ON prayers(pastor_id, created_at);
            CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS login_attempt_window ON login_attempts(client_ip, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS one_admin_account
                ON users(role) WHERE role = 'admin';
            """
        )
        media_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(media_items)").fetchall()
        }
        if "cover_path" not in media_columns:
            db.execute("ALTER TABLE media_items ADD COLUMN cover_path TEXT")
        stream_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(streams)").fetchall()
        }
        if "cover_path" not in stream_columns:
            db.execute("ALTER TABLE streams ADD COLUMN cover_path TEXT")
        profile_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(church_profile)").fetchall()
        }
        if "logo_path" not in profile_columns:
            db.execute("ALTER TABLE church_profile ADD COLUMN logo_path TEXT")
        media_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(media_items)").fetchall()
        }
        if "view_count" not in media_columns:
            db.execute("ALTER TABLE media_items ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0")
        if "auto_discovered" not in media_columns:
            db.execute("ALTER TABLE media_items ADD COLUMN auto_discovered INTEGER NOT NULL DEFAULT 0")
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS unique_auto_discovered_audio_path
               ON media_items(audio_path) WHERE auto_discovered = 1"""
        )
        if "view_count" not in stream_columns:
            db.execute("ALTER TABLE streams ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0")
        db.execute(
            "UPDATE streams SET status = 'interrupted', ended_at = ? WHERE status = 'live'",
            (int(time.time()),),
        )
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute("DELETE FROM studio_sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute(
            """INSERT OR IGNORE INTO church_profile(id, name, updated_at)
               VALUES (1, 'Life Fountain Church', ?)""",
            (int(time.time()),),
        )
        configured_studio_password = os.environ.get("LFC_STUDIO_PASSWORD")
        if configured_studio_password and not db.execute(
            "SELECT 1 FROM studio_credentials WHERE id = 1"
        ).fetchone():
            db.execute(
                "INSERT INTO studio_credentials(id, password_hash) VALUES (1, ?)",
                (password_hash(configured_studio_password),),
            )
    cleanup_expired_sermon_uploads()
    with connect_db() as db:
        admin_exists = db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    setup_code_path = DATA_DIR / ".setup-code"
    if not admin_exists and not setup_code_path.exists():
        setup_code_path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        if os.name == "posix":
            setup_code_path.chmod(0o600)


def cleanup_expired_sermon_uploads() -> None:
    cutoff = int(time.time()) - SERMON_UPLOAD_MAX_AGE_SECONDS
    with connect_db() as db:
        expired = db.execute(
            "SELECT id FROM sermon_uploads WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        db.execute("DELETE FROM sermon_uploads WHERE created_at < ?", (cutoff,))
    for row in expired:
        upload_id = row["id"]
        if not UUID_PATTERN.fullmatch(upload_id):
            continue
        folder = MEDIA_DIR / "large-uploads" / upload_id
        try:
            if folder.is_dir():
                shutil.rmtree(folder)
        except OSError as exc:
            print(f"Could not clean expired sermon upload {upload_id}: {exc}")


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def password_matches(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split(":", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PASSWORD_ITERATIONS
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def safe_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"]}


def valid_password(password: str) -> bool:
    return isinstance(password, str) and 8 <= len(password) <= 256


class ChurchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ChurchHandler(BaseHTTPRequestHandler):
    server_version = "LifeFountain/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; media-src 'self' blob:; connect-src 'self'; "
            "img-src 'self' data:; frame-src https://www.openstreetmap.org; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def send_json(
        self, status: int, payload: dict | list, headers: dict[str, str | list[str]] | None = None
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for name, value in headers.items():
                for header_value in value if isinstance(value, list) else [value]:
                    self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def read_json(self, max_bytes: int = MAX_JSON_BYTES) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length <= 0 or length > max_bytes:
            raise ValueError("Request body is empty or too large.")
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Expected a JSON request.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def authenticated_user(self) -> sqlite3.Row | None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("lfc_session")
        if not morsel:
            return None
        token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
        with connect_db() as db:
            return db.execute(
                """SELECT users.* FROM sessions
                   JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1""",
                (token_hash, int(time.time())),
            ).fetchone()

    def require_staff(self, roles: tuple[str, ...] = ("admin", "pastor")) -> sqlite3.Row | None:
        user = self.authenticated_user()
        if not user:
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Sign in to continue.")
            return None
        if user["role"] not in roles:
            self.send_error_json(HTTPStatus.FORBIDDEN, "You do not have permission to do that.")
            return None
        return user

    def require_studio_access(self) -> bool:
        if self.studio_session_valid():
            return True
        self.send_error_json(HTTPStatus.UNAUTHORIZED, "Unlock the Streaming studio first.")
        return False

    def studio_session_valid(self) -> bool:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("lfc_studio")
        if not morsel:
            return False
        token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
        with connect_db() as db:
            return db.execute(
                "SELECT 1 FROM studio_sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, int(time.time())),
            ).fetchone() is not None

    def unlock_studio(self) -> None:
        body = self.read_json()
        password = body.get("password", "")
        if not isinstance(password, str):
            raise ValueError("Enter the Streaming studio password.")
        now = int(time.time())
        client = self.client_address[0]
        with connect_db() as db:
            db.execute("DELETE FROM login_attempts WHERE created_at < ?", (now - 900,))
            attempts = db.execute(
                """SELECT COUNT(*) AS total FROM login_attempts
                   WHERE client_ip = ? AND email = 'streaming-studio' AND created_at > ?""",
                (client, now - 900),
            ).fetchone()["total"]
            if attempts >= 10:
                self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "Too many studio unlock attempts. Try again later.")
                return
            credential = db.execute(
                "SELECT password_hash FROM studio_credentials WHERE id = 1"
            ).fetchone()
        if not credential:
            self.send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "The Streaming studio password is not configured. Set LFC_STUDIO_PASSWORD and restart the server.",
            )
            return
        if not password_matches(password, credential["password_hash"]):
            with connect_db() as db:
                db.execute(
                    "INSERT INTO login_attempts(client_ip, email, created_at) VALUES (?, 'streaming-studio', ?)",
                    (client, now),
                )
            self.send_error_json(HTTPStatus.FORBIDDEN, "The Streaming studio password is incorrect.")
            return
        with connect_db() as db:
            db.execute(
                "DELETE FROM login_attempts WHERE client_ip = ? AND email = 'streaming-studio'",
                (client,),
            )
            db.execute("DELETE FROM studio_sessions WHERE expires_at < ?", (now,))
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
            expires_at = now + 12 * 60 * 60
            db.execute(
                "INSERT INTO studio_sessions(token_hash, expires_at) VALUES (?, ?)",
                (token_hash, expires_at),
            )
        secure = "; Secure" if os.environ.get("LFC_HTTPS", "").lower() == "1" else ""
        self.send_json(
            HTTPStatus.OK,
            {"ok": True},
            {
                "Set-Cookie": (
                    f"lfc_studio={raw_token}; Path=/; HttpOnly; SameSite=Lax; "
                    f"Max-Age={12 * 60 * 60}{secure}"
                )
            },
        )

    def create_session(self, user_id: str) -> str:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        expires_at = int(time.time()) + SESSION_SECONDS
        with connect_db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
            db.execute(
                "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
        secure = "; Secure" if os.environ.get("LFC_HTTPS", "").lower() == "1" else ""
        return (
            f"lfc_session={raw_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}{secure}"
        )

    def path_parts(self) -> list[str]:
        return [unquote(part) for part in urlparse(self.path).path.split("/") if part]

    def discover_media_files(self) -> None:
        if not MEDIA_DIR.is_dir():
            return
        with connect_db() as db:
            owner = db.execute(
                "SELECT id FROM users WHERE role = 'admin' AND active = 1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not owner:
                return
            stream_ids = {
                row["id"] for row in db.execute("SELECT id FROM streams").fetchall()
            }
            registered_paths = {
                Path(row[field]).resolve()
                for row in db.execute(
                    """SELECT original_path, audio_path FROM media_items
                       WHERE kind = 'sermon'"""
                ).fetchall()
                for field in ("original_path", "audio_path")
                if row[field]
            }
        observed_paths: set[str] = set()
        now = int(time.time())
        media_root = MEDIA_DIR.resolve()

        def report_walk_error(error: OSError) -> None:
            print(f"Could not scan church media directory: {error}")

        for directory, child_directories, filenames in os.walk(MEDIA_DIR, onerror=report_walk_error):
            folder = Path(directory)
            relative_folder = folder.relative_to(MEDIA_DIR)
            if relative_folder.parts:
                if relative_folder.parts[0] in ("media", "church-brand") or relative_folder.parts[0] in stream_ids:
                    child_directories.clear()
                    continue
            child_directories[:] = [
                name for name in child_directories
                if not name.startswith(".") and not (folder / name).is_symlink()
            ]
            for filename in filenames:
                audio_path = folder / filename
                extension = audio_path.suffix.lower()
                if filename.startswith(".") or extension not in DISCOVERABLE_AUDIO_EXTENSIONS:
                    continue
                try:
                    if audio_path.is_symlink():
                        continue
                    file_stat = audio_path.stat()
                    resolved_path = audio_path.resolve(strict=True)
                    resolved_path.relative_to(media_root)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    print(f"Could not inspect sermon file {audio_path}: {exc}")
                    continue
                stored_path = str(resolved_path)
                observed_paths.add(stored_path)
                if resolved_path in registered_paths:
                    cover_path = discover_media_cover(resolved_path)
                    if cover_path:
                        with connect_db() as db:
                            db.execute(
                                """UPDATE media_items SET cover_path = ?
                                   WHERE auto_discovered = 1 AND audio_path = ?
                                     AND cover_path IS NULL""",
                                (str(cover_path), stored_path),
                            )
                    continue
                try:
                    is_complete = discovered_audio_is_complete(resolved_path, file_stat.st_size)
                except OSError as exc:
                    print(f"Could not validate sermon file {resolved_path}: {exc}")
                    continue
                if not is_complete:
                    continue
                with connect_db() as db:
                    candidate = db.execute(
                        """SELECT size, modified_ns, first_seen FROM media_discovery_candidates
                           WHERE file_path = ?""",
                        (stored_path,),
                    ).fetchone()
                    if not candidate or (
                        candidate["size"] != file_stat.st_size
                        or candidate["modified_ns"] != file_stat.st_mtime_ns
                    ):
                        db.execute(
                            """INSERT INTO media_discovery_candidates
                               (file_path, size, modified_ns, first_seen) VALUES (?, ?, ?, ?)
                               ON CONFLICT(file_path) DO UPDATE SET
                                 size = excluded.size, modified_ns = excluded.modified_ns,
                                 first_seen = excluded.first_seen""",
                            (stored_path, file_stat.st_size, file_stat.st_mtime_ns, now),
                        )
                        continue
                    if now - candidate["first_seen"] < MEDIA_DISCOVERY_STABILITY_SECONDS:
                        continue
                    if db.execute(
                        "SELECT 1 FROM media_items WHERE auto_discovered = 1 AND audio_path = ?",
                        (stored_path,),
                    ).fetchone():
                        continue
                    title = re.sub(r"[_\s]+", " ", resolved_path.stem).strip()[:120]
                    title = title or "Untitled sermon"
                    cover_path = discover_media_cover(resolved_path)
                    created_at = int(file_stat.st_mtime)
                    db.execute(
                        """INSERT INTO media_items
                           (id, title, description, kind, mime_type, original_path, audio_path,
                            cover_path, auto_discovered, created_by, created_at)
                           VALUES (?, ?, '', 'sermon', ?, ?, ?, ?, 1, ?, ?)""",
                        (
                            uuid.uuid4().hex,
                            title,
                            DISCOVERABLE_AUDIO_EXTENSIONS[extension],
                            stored_path,
                            stored_path,
                            str(cover_path) if cover_path else None,
                            owner["id"],
                            created_at,
                        ),
                    )
                    registered_paths.add(resolved_path)
                    print(f"Discovered sermon audio: {resolved_path}")
        with connect_db() as db:
            candidates = db.execute(
                "SELECT file_path FROM media_discovery_candidates"
            ).fetchall()
            db.executemany(
                "DELETE FROM media_discovery_candidates WHERE file_path = ?",
                [(row["file_path"],) for row in candidates if row["file_path"] not in observed_paths],
            )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            user = self.authenticated_user()
            with connect_db() as db:
                configured = db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
                live = db.execute(
                    "SELECT id, title, mime_type, started_at FROM streams WHERE status = 'live' LIMIT 1"
                ).fetchone()
            self.send_json(
                HTTPStatus.OK,
                {
                    "setup_required": configured is None,
                    "user": safe_user(user) if user else None,
                    "stream": dict(live) if live else None,
                    "studio_unlocked": self.studio_session_valid(),
                },
            )
            return
        if path == "/api/church-profile":
            with connect_db() as db:
                row = db.execute(
                    """SELECT name, leader, phone, email, address, about, latitude, longitude,
                              logo_path, updated_at
                       FROM church_profile WHERE id = 1"""
                ).fetchone()
            profile = dict(row)
            profile["logo_url"] = f"/api/church-logo?v={profile['updated_at']}" if profile["logo_path"] else None
            del profile["logo_path"]
            del profile["updated_at"]
            self.send_json(HTTPStatus.OK, profile)
            return
        if path == "/api/church-logo":
            with connect_db() as db:
                row = db.execute("SELECT logo_path FROM church_profile WHERE id = 1").fetchone()
            logo_path = Path(row["logo_path"]) if row and row["logo_path"] else None
            if not logo_path or not logo_path.is_file():
                self.send_error_json(HTTPStatus.NOT_FOUND, "Church logo not found.")
                return
            content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(
                logo_path.suffix.lower()
            )
            if not content_type:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Church logo format is invalid.")
                return
            self.send_file(logo_path, content_type)
            return
        if path == "/api/announcements":
            user = self.authenticated_user()
            with connect_db() as db:
                rows = db.execute(
                    """SELECT announcements.id, announcements.title, announcements.message,
                              announcements.audience, announcements.created_at, users.name AS author
                       FROM announcements JOIN users ON users.id = announcements.created_by
                       WHERE announcements.audience = 'everyone' OR ?
                       ORDER BY announcements.created_at DESC LIMIT 100""",
                    (1 if user and user["role"] in ("admin", "pastor") else 0,),
                ).fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        if path == "/api/verses":
            with connect_db() as db:
                rows = db.execute(
                    """SELECT id, text, reference, created_at FROM church_verses
                       ORDER BY created_at DESC, id"""
                ).fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        if path in ("/api/pastors", "/api/pastors/public"):
            user = None
            if path == "/api/pastors":
                user = self.require_staff(("admin",))
                if not user:
                    return
            with connect_db() as db:
                fields = "id, name" if path.endswith("/public") else "id, name, email, role"
                rows = db.execute(
                    f"SELECT {fields} FROM users WHERE role = 'pastor' AND active = 1 ORDER BY name"
                ).fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) if path.endswith("/public") else safe_user(row) for row in rows])
            return
        if path == "/api/prayers":
            user = self.require_staff()
            if not user:
                return
            with connect_db() as db:
                if user["role"] == "admin":
                    rows = db.execute(
                        """SELECT prayers.id, prayers.name, prayers.message, prayers.pastor_id,
                                  prayers.created_at, prayers.response, prayers.responded_at,
                                  users.name AS pastor_name, responder.name AS responder_name
                           FROM prayers LEFT JOIN users ON users.id = prayers.pastor_id
                           LEFT JOIN users AS responder ON responder.id = prayers.responded_by
                           ORDER BY prayers.created_at DESC"""
                    ).fetchall()
                else:
                    rows = db.execute(
                        """SELECT prayers.id, prayers.name, prayers.message, prayers.pastor_id,
                                  prayers.created_at, prayers.response, prayers.responded_at,
                                  users.name AS pastor_name, responder.name AS responder_name
                           FROM prayers LEFT JOIN users ON users.id = prayers.pastor_id
                           LEFT JOIN users AS responder ON responder.id = prayers.responded_by
                           WHERE prayers.pastor_id = ? ORDER BY prayers.created_at DESC""",
                        (user["id"],),
                    ).fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        if path == "/api/stream":
            with connect_db() as db:
                row = db.execute(
                    "SELECT id, title, mime_type, cover_path, started_at FROM streams WHERE status = 'live' LIMIT 1"
                ).fetchone()
            stream = dict(row) if row else None
            if stream:
                stream["cover_url"] = f"/api/streams/{stream['id']}/cover" if stream["cover_path"] else None
                del stream["cover_path"]
            self.send_json(HTTPStatus.OK, {"stream": stream})
            return
        if path == "/api/recordings":
            with connect_db() as db:
                rows = db.execute(
                    """SELECT id, title, mime_type, cover_path, started_at, ended_at, view_count
                       FROM streams WHERE status = 'completed' ORDER BY ended_at DESC LIMIT 100"""
                ).fetchall()
            recordings = []
            for row in rows:
                recording = dict(row)
                recording["cover_url"] = (
                    f"/api/streams/{recording['id']}/cover" if recording["cover_path"] else None
                )
                del recording["cover_path"]
                recordings.append(recording)
            self.send_json(HTTPStatus.OK, recordings)
            return
        if len(self.path_parts()) == 3 and self.path_parts()[:2] == ["api", "sermon-uploads"]:
            self.get_sermon_upload_status()
            return
        if path == "/api/media":
            try:
                self.discover_media_files()
            except sqlite3.Error as exc:
                print(f"Media discovery failed: {type(exc).__name__}: {exc}")
                self.send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "The media library could not be refreshed.",
                )
                return
            with connect_db() as db:
                rows = db.execute(
                    """SELECT media_items.id, media_items.title, media_items.description,
                              media_items.kind, media_items.mime_type, media_items.created_at,
                              media_items.view_count,
                              media_items.cover_path, media_items.auto_discovered,
                              CASE WHEN media_items.auto_discovered = 1
                                   THEN 'Life Fountain Church' ELSE users.name END AS speaker
                       FROM media_items LEFT JOIN users ON users.id = media_items.created_by
                       ORDER BY media_items.created_at DESC LIMIT 200"""
                ).fetchall()
            self.send_json(
                HTTPStatus.OK,
                [
                    {
                        **dict(row),
                        "video_url": f"/api/media/{row['id']}",
                        "audio_url": f"/api/media/{row['id']}/audio" if row["kind"] == "sermon" else None,
                        "view_url": f"/api/media/{row['id']}/view" if row["kind"] == "sermon" else None,
                        "cover_url": f"/api/media/{row['id']}/cover" if row["cover_path"] else None,
                    }
                    for row in rows
                ],
            )
            return
        parts = self.path_parts()
        if len(parts) == 5 and parts[:2] == ["api", "streams"] and parts[3] == "chunks":
            stream_id, sequence_text = parts[2], parts[4]
            if not UUID_PATTERN.fullmatch(stream_id) or not sequence_text.isdecimal():
                self.send_error_json(HTTPStatus.NOT_FOUND, "Audio segment not found.")
                return
            with connect_db() as db:
                row = db.execute(
                    "SELECT file_path FROM audio_chunks WHERE stream_id = ? AND sequence = ?",
                    (stream_id, int(sequence_text)),
                ).fetchone()
            if not row:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Audio segment not found.")
                return
            self.send_file(Path(row["file_path"]), "audio/webm")
            return
        if len(parts) == 3 and parts[:2] == ["api", "recordings"]:
            recording_id = parts[2]
            if not UUID_PATTERN.fullmatch(recording_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Recording not found.")
                return
            with connect_db() as db:
                row = db.execute(
                    "SELECT status, mime_type FROM streams WHERE id = ?", (recording_id,)
                ).fetchone()
            audio_mime_type = row["mime_type"].split(";", 1)[0].strip().lower() if row else ""
            extension = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav"}.get(audio_mime_type)
            audio_path = MEDIA_DIR / recording_id / f"recording.{extension}" if extension else None
            if not row or row["status"] != "completed" or not audio_path or not audio_path.is_file():
                self.send_error_json(HTTPStatus.NOT_FOUND, "Recording not found.")
                return
            self.send_file(audio_path, row["mime_type"])
            return
        if len(parts) == 4 and parts[:2] == ["api", "media"] and parts[3] == "audio":
            media_id = parts[2]
            if not UUID_PATTERN.fullmatch(media_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon audio not found.")
                return
            with connect_db() as db:
                row = db.execute(
                    "SELECT audio_path, mime_type FROM media_items WHERE id = ? AND kind = 'sermon'",
                    (media_id,),
                ).fetchone()
            if not row or not row["audio_path"]:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon audio not found.")
                return
            audio_type = row["mime_type"] if row["mime_type"].startswith("audio/") else "audio/mpeg"
            self.send_file(Path(row["audio_path"]), audio_type)
            return
        if len(parts) == 4 and parts[:2] == ["api", "media"] and parts[3] == "cover":
            media_id = parts[2]
            if not UUID_PATTERN.fullmatch(media_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon cover not found.")
                return
            with connect_db() as db:
                row = db.execute(
                    "SELECT cover_path FROM media_items WHERE id = ?", (media_id,)
                ).fetchone()
            if not row or not row["cover_path"]:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon cover not found.")
                return
            extension = Path(row["cover_path"]).suffix.lower()
            content_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
            }.get(extension)
            if not content_type:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Sermon cover format is invalid.")
                return
            self.send_file(Path(row["cover_path"]), content_type)
            return
        if len(parts) == 3 and parts[:2] == ["api", "media"]:
            media_id = parts[2]
            if not UUID_PATTERN.fullmatch(media_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Media not found.")
                return
            with connect_db() as db:
                row = db.execute(
                    "SELECT original_path, mime_type FROM media_items WHERE id = ?", (media_id,)
                ).fetchone()
            if not row:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Media not found.")
                return
            self.send_file(Path(row["original_path"]), row["mime_type"])
            return
        if len(parts) == 4 and parts[:2] == ["api", "streams"] and parts[3] == "chunks":
            stream_id = parts[2]
            if not UUID_PATTERN.fullmatch(stream_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Stream not found.")
                return
            try:
                after = max(-1, int(parse_qs(parsed.query).get("after", ["-1"])[0]))
            except ValueError:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid audio segment cursor.")
                return
            with connect_db() as db:
                rows = db.execute(
                    """SELECT sequence FROM audio_chunks
                       WHERE stream_id = ? AND sequence > ? ORDER BY sequence LIMIT 128""",
                    (stream_id, after),
                ).fetchall()
            self.send_json(HTTPStatus.OK, {"sequences": [row["sequence"] for row in rows]})
            return
        if len(parts) == 4 and parts[:2] == ["api", "streams"] and parts[3] == "cover":
            stream_id = parts[2]
            if not UUID_PATTERN.fullmatch(stream_id):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Stream wallpaper not found.")
                return
            with connect_db() as db:
                row = db.execute("SELECT cover_path FROM streams WHERE id = ?", (stream_id,)).fetchone()
            if not row or not row["cover_path"]:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Stream wallpaper not found.")
                return
            extension = Path(row["cover_path"]).suffix.lower()
            content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(extension)
            if not content_type:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Stream wallpaper format is invalid.")
                return
            self.send_file(Path(row["cover_path"]), content_type)
            return
        if path in ("/", "/index.html", "/app.js", "/styles.css", "/bible-kjv.json"):
            file_path = ROOT / ("index.html" if path in ("/", "/index.html") else path.lstrip("/"))
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_file(
                file_path,
                content_type,
                "public, max-age=86400" if path == "/bible-kjv.json" else "no-store",
            )
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Page or API route not found.")

    def send_file(self, path: Path, content_type: str, cache_control: str = "no-store") -> None:
        try:
            size = path.stat().st_size
            with path.open("rb") as media:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", cache_control)
                self.end_headers()
                shutil.copyfileobj(media, self.wfile)
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/setup":
                self.setup_admin()
            elif path == "/api/studio/unlock":
                self.unlock_studio()
            elif path == "/api/login":
                self.login()
            elif path == "/api/logout":
                self.logout()
            elif path == "/api/pastors":
                self.add_pastor()
            elif path == "/api/prayers":
                self.create_prayer()
            elif path == "/api/church-profile":
                self.update_church_profile()
            elif path == "/api/church-logo":
                self.upload_church_logo()
            elif path == "/api/announcements":
                self.create_announcement()
            elif path == "/api/sermon-uploads":
                self.initialize_sermon_upload()
            elif re.fullmatch(r"/api/sermon-uploads/[0-9a-f]{32}/(?:chunk|cover|complete)", path):
                upload_action = self.path_parts()[-1]
                if upload_action == "chunk":
                    self.upload_sermon_chunk()
                elif upload_action == "cover":
                    self.upload_sermon_cover()
                else:
                    self.complete_sermon_upload()
            elif re.fullmatch(r"/api/(?:media|recordings)/[0-9a-f]{32}/title", path):
                self.update_sermon_title()
            elif path.startswith("/api/media/") and path.endswith("/view"):
                self.count_media_view()
            elif path.startswith("/api/recordings/") and path.endswith("/view"):
                self.count_recording_view()
            elif path == "/api/verses":
                self.add_church_verse()
            elif path.startswith("/api/prayers/") and path.endswith("/response"):
                self.respond_to_prayer()
            elif path == "/api/streams":
                self.start_stream()
            elif path == "/api/media":
                self.upload_media()
            elif path.startswith("/api/streams/") and path.endswith("/chunks"):
                self.save_stream_chunk()
            elif path.startswith("/api/streams/") and path.endswith("/stop"):
                self.stop_stream()
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found.")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except sqlite3.IntegrityError:
            self.send_error_json(HTTPStatus.CONFLICT, "That email or stream is already in use.")
        except (OSError, sqlite3.Error) as exc:
            print(f"Request failed: {type(exc).__name__}: {exc}")
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "The request could not be saved.")

    def setup_admin(self) -> None:
        body = self.read_json()
        name = body.get("name", "")
        email = body.get("email", "")
        password = body.get("password", "")
        setup_code = body.get("setup_code", "")
        if not all(isinstance(value, str) for value in (name, email, password, setup_code)):
            raise ValueError("Invalid account setup fields.")
        name, email = name.strip(), email.strip()
        try:
            expected_code = (DATA_DIR / ".setup-code").read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.CONFLICT, "Admin setup is already complete.")
            return
        if not hmac.compare_digest(setup_code, expected_code):
            self.send_error_json(HTTPStatus.FORBIDDEN, "The setup code does not match the code printed by the server.")
            return
        if not name or len(name) > 80 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            raise ValueError("Enter a name and a valid email address.")
        if not valid_password(password):
            raise ValueError("Use a password with at least 8 characters.")
        user_id = uuid.uuid4().hex
        with connect_db() as db:
            if db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
                self.send_error_json(HTTPStatus.CONFLICT, "Initial setup is already complete.")
                return
            db.execute(
                "INSERT INTO users(id, name, email, role, password_hash, created_at) VALUES (?, ?, ?, 'admin', ?, ?)",
                (user_id, name, email, password_hash(password), int(time.time())),
            )
        (DATA_DIR / ".setup-code").unlink(missing_ok=True)
        self.send_json(
            HTTPStatus.CREATED,
            {"user": {"id": user_id, "name": name, "email": email, "role": "admin"}},
            {"Set-Cookie": self.create_session(user_id)},
        )

    def login(self) -> None:
        body = self.read_json()
        email = body.get("email", "")
        password = body.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ValueError("Enter your email and password.")
        email = email.strip()
        client = self.client_address[0]
        now = int(time.time())
        with connect_db() as db:
            db.execute("DELETE FROM login_attempts WHERE created_at < ?", (now - 900,))
            attempts = db.execute(
                "SELECT COUNT(*) AS total FROM login_attempts WHERE client_ip = ? AND created_at > ?",
                (client, now - 900),
            ).fetchone()["total"]
            if attempts >= 10:
                self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again later.")
                return
            user = db.execute(
                "SELECT * FROM users WHERE email = ? AND active = 1", (email,)
            ).fetchone()
        if not user or not password_matches(password, user["password_hash"]):
            with connect_db() as db:
                db.execute(
                    "INSERT INTO login_attempts(client_ip, email, created_at) VALUES (?, ?, ?)",
                    (client, email[:254], now),
                )
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Email or password is incorrect.")
            return
        with connect_db() as db:
            db.execute("DELETE FROM login_attempts WHERE client_ip = ?", (client,))
        self.send_json(
            HTTPStatus.OK,
            {"user": safe_user(user)},
            {"Set-Cookie": self.create_session(user["id"])},
        )

    def read_multipart(
        self, require_file: bool = True
    ) -> tuple[dict[str, str], tuple[str, str, bytes] | None, tuple[str, str, bytes] | None]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data;"):
            raise ValueError("Expected a media upload.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid upload size.") from exc
        if length <= 0 or length > MAX_MEDIA_UPLOAD_BYTES + 1024 * 1024:
            raise ValueError("Choose a video smaller than 100 MB.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("The upload was incomplete.")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        if not message.is_multipart():
            raise ValueError("Invalid media upload.")
        fields: dict[str, str] = {}
        upload: tuple[str, str, bytes] | None = None
        cover_upload: tuple[str, str, bytes] | None = None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename is not None:
                if name == "file":
                    upload = (Path(filename.replace("\\", "/")).name, part.get_content_type(), payload)
                elif name == "cover":
                    cover_upload = (Path(filename.replace("\\", "/")).name, part.get_content_type(), payload)
            else:
                fields[name] = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if require_file and not upload:
            raise ValueError("Choose a media file to upload.")
        return fields, upload, cover_upload

    def upload_media(self) -> None:
        user = self.require_staff()
        if not user:
            return
        fields, upload, cover_upload = self.read_multipart()
        filename, mime_type, content = upload
        kind = fields.get("kind", "")
        title = fields.get("title", "").strip()
        description = fields.get("description", "").strip()
        is_direct_audio = kind == "audio-sermon"
        if kind not in ("sermon", "short", "audio-sermon"):
            raise ValueError("Choose a sermon, audio sermon, or short video.")
        if not filename or not content:
            raise ValueError("Choose a valid media file.")
        if is_direct_audio:
            audio_formats = {
                ".mp3": ({"audio/mpeg", "audio/mp3"}, content.startswith(b"ID3") or
                         (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0)),
                ".wav": ({"audio/wav", "audio/x-wav"}, content.startswith(b"RIFF") and content[8:12] == b"WAVE"),
                ".m4a": ({"audio/mp4", "audio/x-m4a"}, content[4:8] == b"ftyp"),
                ".mp4": ({"audio/mp4"}, content[4:8] == b"ftyp"),
                ".ogg": ({"audio/ogg", "application/ogg"}, content.startswith(b"OggS")),
                ".oga": ({"audio/ogg", "application/ogg"}, content.startswith(b"OggS")),
                ".webm": ({"audio/webm"}, content.startswith(b"\x1aE\xdf\xa3")),
                ".flac": ({"audio/flac", "audio/x-flac"}, content.startswith(b"fLaC")),
                ".aac": ({"audio/aac", "audio/aacp"}, len(content) > 1 and content[0] == 0xFF and content[1] & 0xF6 == 0xF0),
            }
            audio_format = audio_formats.get(Path(filename).suffix.lower())
            if not audio_format or mime_type.lower() not in audio_format[0] or not audio_format[1]:
                raise ValueError("Choose a supported audio file (MP3, WAV, M4A, OGG, WebM, FLAC, or AAC).")
            mime_type = "audio/mp4" if Path(filename).suffix.lower() in (".m4a", ".mp4") else (
                "audio/ogg" if Path(filename).suffix.lower() in (".oga", ".ogg") else mime_type.lower()
            )
        elif not mime_type.startswith("video/"):
            raise ValueError("Choose a valid video file.")
        if len(content) > MAX_MEDIA_UPLOAD_BYTES:
            raise ValueError("Choose a media file smaller than 100 MB.")
        if cover_upload:
            cover_filename, cover_mime, cover_data = cover_upload
            cover_signatures = {
                "image/jpeg": cover_data.startswith(b"\xff\xd8\xff"),
                "image/png": cover_data.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/webp": cover_data.startswith(b"RIFF") and cover_data[8:12] == b"WEBP",
            }
            if (
                not cover_filename
                or cover_mime not in cover_signatures
                or not cover_signatures[cover_mime]
                or not cover_data
            ):
                raise ValueError("Choose a valid JPEG, PNG, or WebP wallpaper.")
            if len(cover_data) > 5 * 1024 * 1024:
                raise ValueError("Choose a wallpaper smaller than 5 MB.")
        elif kind == "sermon" and fields.get("cover"):
            raise ValueError("Choose a valid JPEG, PNG, or WebP wallpaper.")
        title = title[:120] or Path(filename).stem[:120]
        description = description[:2000]
        media_id = uuid.uuid4().hex
        folder = MEDIA_DIR / "media" / media_id
        folder.mkdir(parents=True, exist_ok=False)
        suffix = Path(filename).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".video"
        original_path = folder / f"original{suffix}"
        original_path.write_bytes(content)
        cover_path: Path | None = None
        if cover_upload:
            cover_extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
            cover_path = folder / f"cover{cover_extensions[cover_upload[1]]}"
            cover_path.write_bytes(cover_upload[2])
        audio_path: Path | None = None
        try:
            if is_direct_audio:
                audio_path = original_path
                original_path_for_database = str(original_path)
                kind = "sermon"
            elif kind == "sermon":
                ffmpeg_config = os.environ.get("LFC_FFMPEG_PATH") or os.environ.get("FFMPEG_PATH", "ffmpeg")
                ffmpeg = str(Path(ffmpeg_config)) if Path(ffmpeg_config).is_file() else shutil.which(ffmpeg_config)
                if not ffmpeg:
                    original_path.unlink(missing_ok=True)
                    folder.rmdir()
                    self.send_error_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "Sermon conversion needs FFmpeg. Install FFmpeg, restart the server, and try again.",
                    )
                    return
                audio_path = folder / "sermon.mp3"
                try:
                    result = subprocess.run(
                        [
                            ffmpeg,
                            "-nostdin",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-y",
                            "-i",
                            str(original_path),
                            "-vn",
                            "-codec:a",
                            "libmp3lame",
                            "-b:a",
                            "128k",
                            str(audio_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=900,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    original_path.unlink(missing_ok=True)
                    shutil.rmtree(folder, ignore_errors=True)
                    raise ValueError("Sermon audio conversion took too long. Try a shorter video.") from exc
                if result.returncode != 0 or not audio_path.is_file():
                    original_path.unlink(missing_ok=True)
                    shutil.rmtree(folder, ignore_errors=True)
                    detail = result.stderr.strip().splitlines()[-1][:250] if result.stderr.strip() else "Unknown conversion error."
                    raise ValueError(f"Could not convert the sermon video: {detail}")
                original_path.unlink()
                original_path_for_database = ""
            else:
                original_path_for_database = str(original_path)
            with connect_db() as db:
                db.execute(
                    """INSERT INTO media_items
                       (id, title, description, kind, mime_type, original_path, audio_path, cover_path, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        media_id,
                        title,
                        description,
                        kind,
                        mime_type,
                        original_path_for_database,
                        str(audio_path) if audio_path else None,
                        str(cover_path) if cover_path else None,
                        user["id"],
                        int(time.time()),
                    ),
                )
        except (OSError, sqlite3.Error):
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self.send_json(
            HTTPStatus.CREATED,
            {
                "id": media_id,
                "title": title,
                "kind": kind,
                "video_url": f"/api/media/{media_id}" if kind == "short" else None,
                "audio_url": f"/api/media/{media_id}/audio" if audio_path else None,
                "cover_url": f"/api/media/{media_id}/cover" if cover_path else None,
            },
        )

    def sermon_upload_lock(self, upload_id: str) -> threading.Lock:
        with SERMON_UPLOAD_LOCKS_GUARD:
            return SERMON_UPLOAD_LOCKS.setdefault(upload_id, threading.Lock())

    def owned_sermon_upload(self, upload_id: str, user: sqlite3.Row) -> sqlite3.Row | None:
        with connect_db() as db:
            upload = db.execute(
                "SELECT * FROM sermon_uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        if not upload:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return None
        if user["role"] != "admin" and upload["owner_id"] != user["id"]:
            self.send_error_json(HTTPStatus.FORBIDDEN, "Only the uploader or an admin can manage this sermon upload.")
            return None
        return upload

    def initialize_sermon_upload(self) -> None:
        user = self.require_staff()
        if not user:
            return
        cleanup_expired_sermon_uploads()
        body = self.read_json()
        filename = body.get("filename")
        mime_type = body.get("mime_type")
        file_size = body.get("file_size")
        title = body.get("title")
        if not isinstance(filename, str) or not filename.strip() or not isinstance(mime_type, str):
            raise ValueError("Choose a video file to convert.")
        safe_filename = Path(filename.replace("\\", "/")).name
        extension = Path(safe_filename).suffix.lower()
        if (
            not safe_filename
            or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension)
            or not mime_type.startswith("video/")
        ):
            raise ValueError("Choose a supported video file to convert.")
        if type(file_size) is not int or file_size <= 0 or file_size > MAX_VIDEO_SERMON_UPLOAD_BYTES:
            raise ValueError("Choose a video file up to 20 GB.")
        if not isinstance(title, str) or len(title.strip()) > 120:
            raise ValueError("Enter a sermon title up to 120 characters.")
        clean_title = title.strip() or Path(safe_filename).stem[:120] or "Sermon"
        free_space = shutil.disk_usage(MEDIA_DIR).free
        if file_size > free_space:
            raise ValueError("There is not enough free disk space to receive this video file.")
        upload_id = uuid.uuid4().hex
        upload_folder = MEDIA_DIR / "large-uploads" / upload_id
        upload_folder.mkdir(parents=True, exist_ok=False)
        try:
            with connect_db() as db:
                db.execute(
                    """INSERT INTO sermon_uploads
                       (id, owner_id, original_filename, title, mime_type, file_size, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        upload_id,
                        user["id"],
                        safe_filename,
                        clean_title,
                        mime_type.lower(),
                        file_size,
                        int(time.time()),
                    ),
                )
        except (OSError, sqlite3.Error):
            shutil.rmtree(upload_folder, ignore_errors=True)
            raise
        self.send_json(
            HTTPStatus.CREATED,
            {
                "id": upload_id,
                "chunk_size": SERMON_UPLOAD_CHUNK_BYTES,
                "uploaded_bytes": 0,
                "file_size": file_size,
            },
        )

    def get_sermon_upload_status(self) -> None:
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        upload_id = parts[2]
        if not UUID_PATTERN.fullmatch(upload_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return
        upload = self.owned_sermon_upload(upload_id, user)
        if not upload:
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "id": upload["id"],
                "uploaded_bytes": upload["uploaded_bytes"],
                "file_size": upload["file_size"],
            },
        )

    def upload_sermon_chunk(self) -> None:
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        upload_id = parts[2]
        if not UUID_PATTERN.fullmatch(upload_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return
        upload = self.owned_sermon_upload(upload_id, user)
        if not upload:
            return
        try:
            offset = int(self.headers.get("X-Upload-Offset", "-1"))
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid large-file upload offset or chunk size.") from exc
        if offset != upload["uploaded_bytes"] or offset >= upload["file_size"]:
            self.send_error_json(HTTPStatus.CONFLICT, "The video upload offset is out of date.")
            return
        expected_length = min(SERMON_UPLOAD_CHUNK_BYTES, upload["file_size"] - offset)
        if length != expected_length:
            raise ValueError("The video upload chunk has an invalid size.")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() not in (
            "application/octet-stream",
            upload["mime_type"].split(";", 1)[0].strip(),
        ):
            raise ValueError("Invalid video upload chunk content type.")
        upload_folder = MEDIA_DIR / "large-uploads" / upload_id
        suffix = Path(upload["original_filename"]).suffix.lower()
        source_path = upload_folder / f"source{suffix}"
        temp_chunk_path = upload_folder / "chunk.tmp"
        lock = self.sermon_upload_lock(upload_id)
        with lock:
            with connect_db() as db:
                latest_upload = db.execute(
                    "SELECT uploaded_bytes FROM sermon_uploads WHERE id = ?",
                    (upload_id,),
                ).fetchone()
            if not latest_upload:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
                return
            if latest_upload["uploaded_bytes"] != offset:
                self.send_error_json(HTTPStatus.CONFLICT, "The video upload offset is out of date.")
                return
            upload = {**dict(upload), "uploaded_bytes": latest_upload["uploaded_bytes"]}
            if source_path.is_symlink() or temp_chunk_path.is_symlink():
                raise ValueError("The temporary video upload path is invalid.")
            if source_path.exists() and source_path.stat().st_size != upload["uploaded_bytes"]:
                with source_path.open("r+b") as source_file:
                    source_file.truncate(upload["uploaded_bytes"])
            received = 0
            try:
                with temp_chunk_path.open("wb") as chunk_file:
                    while received < length:
                        chunk = self.rfile.read(min(1024 * 1024, length - received))
                        if not chunk:
                            raise ValueError("The video upload chunk was incomplete. Retry this chunk.")
                        chunk_file.write(chunk)
                        received += len(chunk)
                with temp_chunk_path.open("rb") as chunk_file, source_path.open("ab") as source_file:
                    shutil.copyfileobj(chunk_file, source_file, length=1024 * 1024)
                uploaded_bytes = offset + length
                with connect_db() as db:
                    cursor = db.execute(
                        """UPDATE sermon_uploads SET uploaded_bytes = ?, created_at = ?
                           WHERE id = ? AND uploaded_bytes = ?""",
                        (uploaded_bytes, int(time.time()), upload_id, offset),
                    )
                    if cursor.rowcount != 1:
                        raise sqlite3.IntegrityError("Sermon upload offset changed during chunk write.")
            finally:
                temp_chunk_path.unlink(missing_ok=True)
        self.send_json(
            HTTPStatus.OK,
            {"id": upload_id, "uploaded_bytes": uploaded_bytes, "file_size": upload["file_size"]},
        )

    def upload_sermon_cover(self) -> None:
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        upload_id = parts[2]
        if not UUID_PATTERN.fullmatch(upload_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return
        if not self.owned_sermon_upload(upload_id, user):
            return
        _, cover_upload, _ = self.read_multipart()
        if not cover_upload:
            raise ValueError("Choose a sermon wallpaper.")
        filename, mime_type, content = cover_upload
        signatures = {
            "image/jpeg": (".jpg", content.startswith(b"\xff\xd8\xff")),
            "image/png": (".png", content.startswith(b"\x89PNG\r\n\x1a\n")),
            "image/webp": (".webp", content.startswith(b"RIFF") and content[8:12] == b"WEBP"),
        }
        if mime_type not in signatures or not signatures[mime_type][1] or not filename:
            raise ValueError("Choose a valid JPEG, PNG, or WebP wallpaper.")
        if not content or len(content) > 5 * 1024 * 1024:
            raise ValueError("Choose a wallpaper smaller than 5 MB.")
        upload_folder = MEDIA_DIR / "large-uploads" / upload_id
        cover_path = upload_folder / f"cover{signatures[mime_type][0]}"
        cover_path.write_bytes(content)
        with connect_db() as db:
            db.execute(
                "UPDATE sermon_uploads SET cover_path = ? WHERE id = ?",
                (str(cover_path), upload_id),
            )
        self.send_json(HTTPStatus.OK, {"ok": True})

    def complete_sermon_upload(self) -> None:
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        upload_id = parts[2]
        if not UUID_PATTERN.fullmatch(upload_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return
        upload = self.owned_sermon_upload(upload_id, user)
        if not upload:
            return
        lock = self.sermon_upload_lock(upload_id)
        with lock:
            suffix = Path(upload["original_filename"]).suffix.lower()
            source_path = MEDIA_DIR / "large-uploads" / upload_id / f"source{suffix}"
            if (
                upload["uploaded_bytes"] != upload["file_size"]
                or not source_path.is_file()
                or source_path.stat().st_size != upload["file_size"]
            ):
                self.send_error_json(HTTPStatus.CONFLICT, "The video upload is incomplete.")
                return
            ffmpeg_config = os.environ.get("LFC_FFMPEG_PATH") or os.environ.get("FFMPEG_PATH", "ffmpeg")
            ffmpeg = str(Path(ffmpeg_config)) if Path(ffmpeg_config).is_file() else shutil.which(ffmpeg_config)
            if not ffmpeg:
                self.send_error_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Sermon conversion needs FFmpeg. Install FFmpeg, restart the server, and try again.",
                )
                return
            media_id = uuid.uuid4().hex
            media_folder = MEDIA_DIR / "media" / media_id
            media_folder.mkdir(parents=True, exist_ok=False)
            audio_path = media_folder / "sermon.mp3"
            try:
                result = subprocess.run(
                    [
                        ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source_path),
                        "-vn",
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        str(audio_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=24 * 60 * 60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                shutil.rmtree(media_folder, ignore_errors=True)
                raise ValueError("Converting this video took longer than 24 hours.") from exc
            if result.returncode != 0 or not audio_path.is_file():
                shutil.rmtree(media_folder, ignore_errors=True)
                detail = result.stderr.strip().splitlines()[-1][:250] if result.stderr.strip() else "Unknown conversion error."
                raise ValueError(f"Could not convert the sermon video: {detail}")
            cover_path: Path | None = None
            if upload["cover_path"]:
                source_cover_path = Path(upload["cover_path"])
                cover_path = media_folder / source_cover_path.name
                os.replace(source_cover_path, cover_path)
            try:
                with connect_db() as db:
                    db.execute(
                        """INSERT INTO media_items
                           (id, title, description, kind, mime_type, original_path, audio_path,
                            cover_path, created_by, created_at)
                           VALUES (?, ?, '', 'sermon', ?, '', ?, ?, ?, ?)""",
                        (
                            media_id,
                            upload["title"],
                            upload["mime_type"],
                            str(audio_path),
                            str(cover_path) if cover_path else None,
                            user["id"],
                            int(time.time()),
                        ),
                    )
                    db.execute("DELETE FROM sermon_uploads WHERE id = ?", (upload_id,))
            except (OSError, sqlite3.Error):
                shutil.rmtree(media_folder, ignore_errors=True)
                raise
            upload_folder = MEDIA_DIR / "large-uploads" / upload_id
            try:
                shutil.rmtree(upload_folder)
            except OSError as exc:
                print(f"Could not clean completed sermon upload {upload_id}: {exc}")
        self.send_json(
            HTTPStatus.CREATED,
            {
                "id": media_id,
                "title": upload["title"],
                "kind": "sermon",
                "audio_url": f"/api/media/{media_id}/audio",
                "cover_url": f"/api/media/{media_id}/cover" if cover_path else None,
            },
        )

    def cancel_sermon_upload(self) -> None:
        user = self.require_staff()
        if not user:
            return
        upload_id = self.path_parts()[2]
        if not UUID_PATTERN.fullmatch(upload_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Large sermon upload not found.")
            return
        upload = self.owned_sermon_upload(upload_id, user)
        if not upload:
            return
        with connect_db() as db:
            db.execute("DELETE FROM sermon_uploads WHERE id = ?", (upload_id,))
        upload_folder = MEDIA_DIR / "large-uploads" / upload_id
        if upload_folder.is_dir():
            shutil.rmtree(upload_folder)
        self.send_json(HTTPStatus.OK, {"ok": True})

    def logout(self) -> None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("lfc_session")
        if morsel:
            token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
            with connect_db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        studio_cookie = cookie.get("lfc_studio")
        if studio_cookie:
            studio_hash = hashlib.sha256(studio_cookie.value.encode("ascii", "ignore")).hexdigest()
            with connect_db() as db:
                db.execute("DELETE FROM studio_sessions WHERE token_hash = ?", (studio_hash,))
        self.send_json(
            HTTPStatus.OK,
            {"ok": True},
            {
                "Set-Cookie": [
                    "lfc_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
                    "lfc_studio=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
                ]
            },
        )

    def add_pastor(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        body = self.read_json()
        name = body.get("name", "").strip()
        email = body.get("email", "").strip()
        password = body.get("password", "")
        if not name or len(name) > 80 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            raise ValueError("Enter a pastor name and valid email address.")
        if not valid_password(password):
            raise ValueError("Set a temporary password with at least 8 characters.")
        user_id = uuid.uuid4().hex
        with connect_db() as db:
            db.execute(
                "INSERT INTO users(id, name, email, role, password_hash, created_at) VALUES (?, ?, ?, 'pastor', ?, ?)",
                (user_id, name, email, password_hash(password), int(time.time())),
            )
        self.send_json(
            HTTPStatus.CREATED,
            {"id": user_id, "name": name, "email": email, "role": "pastor"},
        )

    def create_prayer(self) -> None:
        body = self.read_json()
        name = str(body.get("name", "")).strip()[:80] or "Church member"
        message = str(body.get("message", "")).strip()
        pastor_id = str(body.get("pastor_id", "")).strip() or None
        if not message or len(message) > 5000:
            raise ValueError("Write a prayer request (up to 5,000 characters).")
        client = self.client_address[0]
        with connect_db() as db:
            recent = db.execute(
                "SELECT COUNT(*) AS total FROM prayers WHERE client_ip = ? AND created_at > ?",
                (client, int(time.time()) - 600),
            ).fetchone()["total"]
            if recent >= 8:
                self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "Please wait before submitting another request.")
                return
            if pastor_id and not db.execute(
                "SELECT 1 FROM users WHERE id = ? AND role = 'pastor' AND active = 1", (pastor_id,)
            ).fetchone():
                raise ValueError("Choose an active pastor or the pastoral care team.")
            request_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO prayers(id, name, message, pastor_id, client_ip, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, name, message, pastor_id, client, int(time.time())),
            )
        self.send_json(HTTPStatus.CREATED, {"ok": True, "id": request_id})

    def upload_church_logo(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        _, upload, _ = self.read_multipart()
        if not upload:
            raise ValueError("Choose a church logo to upload.")
        filename, mime_type, content = upload
        signatures = {
            "image/jpeg": (".jpg", content.startswith(b"\xff\xd8\xff")),
            "image/png": (".png", content.startswith(b"\x89PNG\r\n\x1a\n")),
            "image/webp": (".webp", content.startswith(b"RIFF") and content[8:12] == b"WEBP"),
        }
        if mime_type not in signatures or not signatures[mime_type][1] or not filename:
            raise ValueError("Choose a valid JPG, PNG, or WebP logo.")
        if not content or len(content) > 2 * 1024 * 1024:
            raise ValueError("Choose a logo smaller than 2 MB.")
        logo_folder = MEDIA_DIR / "church-brand"
        logo_folder.mkdir(parents=True, exist_ok=True)
        logo_path = logo_folder / f"logo-{uuid.uuid4().hex}{signatures[mime_type][0]}"
        logo_path.write_bytes(content)
        try:
            with connect_db() as db:
                previous = db.execute(
                    "SELECT logo_path FROM church_profile WHERE id = 1"
                ).fetchone()["logo_path"]
                updated_at = int(time.time())
                db.execute(
                    "UPDATE church_profile SET logo_path = ?, updated_at = ? WHERE id = 1",
                    (str(logo_path), updated_at),
                )
        except sqlite3.Error:
            logo_path.unlink(missing_ok=True)
            raise
        if previous and Path(previous) != logo_path:
            Path(previous).unlink(missing_ok=True)
        self.send_json(
            HTTPStatus.CREATED,
            {"logo_url": f"/api/church-logo?v={updated_at}"},
        )

    def delete_church_logo(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        with connect_db() as db:
            row = db.execute(
                "SELECT logo_path FROM church_profile WHERE id = 1"
            ).fetchone()
            db.execute(
                "UPDATE church_profile SET logo_path = NULL, updated_at = ? WHERE id = 1",
                (int(time.time()),),
            )
        if row and row["logo_path"]:
            Path(row["logo_path"]).unlink(missing_ok=True)
        self.send_json(HTTPStatus.OK, {"ok": True})

    def create_announcement(self) -> None:
        staff = self.require_staff()
        if not staff:
            return
        body = self.read_json()
        title = body.get("title", "")
        message = body.get("message", "")
        audience = body.get("audience", "")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ValueError("Enter an announcement heading up to 120 characters.")
        if not isinstance(message, str) or not message.strip() or len(message.strip()) > 3000:
            raise ValueError("Write an announcement message up to 3,000 characters.")
        if audience not in ("everyone", "pastors"):
            raise ValueError("Choose whether the announcement is for everyone or pastors.")
        announcement_id = uuid.uuid4().hex
        created_at = int(time.time())
        with connect_db() as db:
            db.execute(
                """INSERT INTO announcements(id, title, message, audience, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (announcement_id, title.strip(), message.strip(), audience, staff["id"], created_at),
            )
        self.send_json(
            HTTPStatus.CREATED,
            {
                "id": announcement_id,
                "title": title.strip(),
                "message": message.strip(),
                "audience": audience,
                "created_at": created_at,
                "author": staff["name"],
            },
        )

    def count_media_view(self) -> None:
        parts = self.path_parts()
        if len(parts) != 4 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
            return
        with connect_db() as db:
            db.execute(
                "UPDATE media_items SET view_count = view_count + 1 WHERE id = ? AND kind = 'sermon'",
                (parts[2],),
            )
            row = db.execute(
                "SELECT view_count FROM media_items WHERE id = ? AND kind = 'sermon'",
                (parts[2],),
            ).fetchone()
        if not row:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
            return
        self.send_json(HTTPStatus.OK, {"view_count": row["view_count"]})

    def count_recording_view(self) -> None:
        parts = self.path_parts()
        if len(parts) != 4 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Recording not found.")
            return
        with connect_db() as db:
            db.execute(
                """UPDATE streams SET view_count = view_count + 1
                   WHERE id = ? AND status = 'completed'""",
                (parts[2],),
            )
            row = db.execute(
                "SELECT view_count FROM streams WHERE id = ? AND status = 'completed'",
                (parts[2],),
            ).fetchone()
        if not row:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Recording not found.")
            return
        self.send_json(HTTPStatus.OK, {"view_count": row["view_count"]})

    def update_sermon_title(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        parts = self.path_parts()
        if len(parts) != 4 or parts[3] != "title" or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
            return
        body = self.read_json()
        title = body.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ValueError("Enter a sermon title up to 120 characters.")
        is_recording = parts[1] == "recordings"
        with connect_db() as db:
            if is_recording:
                cursor = db.execute(
                    "UPDATE streams SET title = ? WHERE id = ? AND status = 'completed'",
                    (title.strip(), parts[2]),
                )
            else:
                cursor = db.execute(
                    "UPDATE media_items SET title = ? WHERE id = ? AND kind = 'sermon'",
                    (title.strip(), parts[2]),
                )
            if cursor.rowcount == 0:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
                return
        self.send_json(HTTPStatus.OK, {"id": parts[2], "title": title.strip()})

    def update_church_profile(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        body = self.read_json()
        fields = {}
        for key in ("name", "leader", "phone", "email", "address", "about"):
            value = body.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"Invalid church {key}.")
            fields[key] = value.strip()
        if not fields["name"] or len(fields["name"]) > 120:
            raise ValueError("Enter a church name up to 120 characters.")
        if len(fields["leader"]) > 120 or len(fields["phone"]) > 40:
            raise ValueError("Contact name or phone number is too long.")
        if fields["email"] and (
            len(fields["email"]) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["email"])
        ):
            raise ValueError("Enter a valid contact email address.")
        if len(fields["address"]) > 500 or len(fields["about"]) > 3000:
            raise ValueError("Church address or About text is too long.")

        coordinates = []
        for key in ("latitude", "longitude"):
            value = body.get(key)
            if value is None or value == "":
                coordinates.append(None)
                continue
            try:
                coordinates.append(float(value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Enter a valid map {key}.") from exc
        latitude, longitude = coordinates
        if (latitude is None) != (longitude is None):
            raise ValueError("Enter both map coordinates to place a pin.")
        if latitude is not None and (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
        ):
            raise ValueError("Map coordinates are outside the valid range.")
        with connect_db() as db:
            db.execute(
                """UPDATE church_profile SET name = ?, leader = ?, phone = ?, email = ?,
                   address = ?, about = ?, latitude = ?, longitude = ?, updated_at = ?
                   WHERE id = 1""",
                (
                    fields["name"],
                    fields["leader"],
                    fields["phone"],
                    fields["email"],
                    fields["address"],
                    fields["about"],
                    latitude,
                    longitude,
                    int(time.time()),
                ),
            )
        self.send_json(HTTPStatus.OK, {"ok": True})

    def add_church_verse(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        body = self.read_json()
        text = body.get("text", "")
        reference = body.get("reference", "")
        if not isinstance(text, str) or not isinstance(reference, str):
            raise ValueError("Enter a verse and Bible reference.")
        text, reference = text.strip(), reference.strip()
        if not text or len(text) > 2000 or not reference or len(reference) > 80:
            raise ValueError("Enter verse text (up to 2,000 characters) and a reference (up to 80 characters).")
        verse_id = uuid.uuid4().hex
        created_at = int(time.time())
        with connect_db() as db:
            db.execute(
                "INSERT INTO church_verses(id, text, reference, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (verse_id, text, reference, admin["id"], created_at),
            )
        self.send_json(
            HTTPStatus.CREATED,
            {"id": verse_id, "text": text, "reference": reference, "created_at": created_at},
        )

    def respond_to_prayer(self) -> None:
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        if len(parts) != 4 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Prayer request not found.")
            return
        body = self.read_json()
        response = str(body.get("response", "")).strip()
        if not response or len(response) > 5000:
            raise ValueError("Write a response (up to 5,000 characters).")
        with connect_db() as db:
            prayer = db.execute("SELECT pastor_id FROM prayers WHERE id = ?", (parts[2],)).fetchone()
            if not prayer:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Prayer request not found.")
                return
            if user["role"] == "pastor" and prayer["pastor_id"] != user["id"]:
                self.send_error_json(HTTPStatus.FORBIDDEN, "You can only respond to requests assigned to you.")
                return
            db.execute(
                "UPDATE prayers SET response = ?, responded_by = ?, responded_at = ? WHERE id = ?",
                (response, user["id"], int(time.time()), parts[2]),
            )
        self.send_json(HTTPStatus.OK, {"ok": True})

    def start_stream(self) -> None:
        if not self.require_studio_access():
            return
        user = self.require_staff()
        if not user:
            return
        cover_upload = None
        if self.headers.get("Content-Type", "").startswith("multipart/form-data;"):
            fields, upload, cover_upload = self.read_multipart(require_file=False)
            if upload:
                raise ValueError("A live stream accepts a wallpaper, not a video file.")
            title = fields.get("title", "").strip()[:120]
            mime_type = fields.get("mime_type", "")
        else:
            body = self.read_json()
            title = str(body.get("title", "")).strip()[:120]
            mime_type = body.get("mime_type", "")
        if not title:
            raise ValueError("Enter a title for the stream.")
        if mime_type not in ("audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"):
            raise ValueError("This browser's audio format is not supported.")
        if cover_upload:
            cover_filename, cover_mime, cover_data = cover_upload
            cover_signatures = {
                "image/jpeg": cover_data.startswith(b"\xff\xd8\xff"),
                "image/png": cover_data.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/webp": cover_data.startswith(b"RIFF") and cover_data[8:12] == b"WEBP",
            }
            if (
                not cover_filename
                or cover_mime not in cover_signatures
                or not cover_signatures[cover_mime]
                or not cover_data
            ):
                raise ValueError("Choose a valid JPEG, PNG, or WebP wallpaper.")
            if len(cover_data) > 5 * 1024 * 1024:
                raise ValueError("Choose a wallpaper smaller than 5 MB.")
        stream_id = uuid.uuid4().hex
        folder = MEDIA_DIR / stream_id
        folder.mkdir(parents=True, exist_ok=False)
        cover_path = None
        if cover_upload:
            cover_extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
            cover_path = folder / f"cover{cover_extensions[cover_upload[1]]}"
            cover_path.write_bytes(cover_upload[2])
        try:
            with connect_db() as db:
                db.execute(
                    """INSERT INTO streams(id, title, mime_type, cover_path, owner_id, started_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'live')""",
                    (
                        stream_id,
                        title,
                        mime_type,
                        str(cover_path) if cover_path else None,
                        user["id"],
                        int(time.time()),
                    ),
                )
        except sqlite3.IntegrityError:
            shutil.rmtree(folder)
            raise
        self.send_json(
            HTTPStatus.CREATED,
            {
                "id": stream_id,
                "title": title,
                "mime_type": mime_type,
                "cover_url": f"/api/streams/{stream_id}/cover" if cover_path else None,
            },
        )

    def stream_owner(self, stream_id: str, user: sqlite3.Row) -> sqlite3.Row | None:
        with connect_db() as db:
            stream = db.execute("SELECT * FROM streams WHERE id = ?", (stream_id,)).fetchone()
        if not stream:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Stream not found.")
            return None
        if stream["status"] != "live":
            self.send_error_json(HTTPStatus.CONFLICT, "This stream is no longer live.")
            return None
        if user["role"] != "admin" and stream["owner_id"] != user["id"]:
            self.send_error_json(HTTPStatus.FORBIDDEN, "Only the streamer or an admin can control this stream.")
            return None
        return stream

    def save_stream_chunk(self) -> None:
        if not self.require_studio_access():
            return
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        if len(parts) != 4 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Stream not found.")
            return
        stream_id = parts[2]
        stream = self.stream_owner(stream_id, user)
        if not stream:
            return
        try:
            sequence = int(self.headers.get("X-Audio-Sequence", "-1"))
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid audio segment.")
        if sequence < 0 or length <= 0 or length > MAX_AUDIO_CHUNK_BYTES:
            raise ValueError("Audio segment is empty or too large.")
        if self.headers.get("Content-Type", "").split(";")[0] not in ("audio/webm", "audio/ogg"):
            raise ValueError("Unsupported audio segment format.")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("Audio segment upload was incomplete.")
        chunk_path = MEDIA_DIR / stream_id / f"{sequence:08d}.part"
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        with chunk_path.open("xb") as chunk_file:
            chunk_file.write(data)
        try:
            with connect_db() as db:
                db.execute(
                    "INSERT INTO audio_chunks(stream_id, sequence, file_path) VALUES (?, ?, ?)",
                    (stream_id, sequence, str(chunk_path)),
                )
        except sqlite3.IntegrityError:
            chunk_path.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.CONFLICT, "Audio segment was already received.")
            return
        self.send_json(HTTPStatus.CREATED, {"sequence": sequence})

    def stop_stream(self) -> None:
        if not self.require_studio_access():
            return
        user = self.require_staff()
        if not user:
            return
        parts = self.path_parts()
        if len(parts) != 4 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Stream not found.")
            return
        stream_id = parts[2]
        stream = self.stream_owner(stream_id, user)
        if not stream:
            return
        folder = MEDIA_DIR / stream_id
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        recording_extensions = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav"}
        recording_mime_type = content_type or stream["mime_type"].split(";", 1)[0].strip().lower()
        if content_type in recording_extensions:
            if content_type != "audio/wav" and content_type != stream["mime_type"].split(";", 1)[0].strip().lower():
                raise ValueError("The saved recording format does not match the live stream.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid recording length.") from exc
            if length <= 4 or length > MAX_RECORDING_BYTES:
                raise ValueError("The saved recording is empty or larger than 500 MB.")
            expected_headers = {"audio/webm": b"\x1aE\xdf\xa3", "audio/ogg": b"OggS", "audio/wav": b"RIFF"}
            header = b""
            extension = recording_extensions[content_type]
            recording_path = folder / f"recording.{extension}"
            temporary_path = folder / f"recording.{extension}.tmp"
            try:
                with temporary_path.open("wb") as recording:
                    header_size = 12 if content_type == "audio/wav" else 4
                    header = self.rfile.read(header_size)
                    recording.write(header)
                    remaining = length - len(header)
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("The saved recording upload was incomplete.")
                        recording.write(chunk)
                        remaining -= len(chunk)
                    valid_header = header[:4] == expected_headers[content_type]
                    if content_type == "audio/wav":
                        valid_header = valid_header and header[8:12] == b"WAVE"
                    if len(header) != header_size or not valid_header:
                        raise ValueError("The saved recording has an invalid audio file header.")
                os.replace(temporary_path, recording_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            with connect_db() as db:
                chunks = db.execute(
                    "SELECT file_path FROM audio_chunks WHERE stream_id = ? ORDER BY sequence", (stream_id,)
                ).fetchall()
            if not chunks:
                self.send_error_json(HTTPStatus.CONFLICT, "The stream has no uploaded audio segments yet.")
                return
            recording_mime_type = stream["mime_type"].split(";", 1)[0].strip().lower()
            extension = recording_extensions.get(recording_mime_type)
            if not extension:
                self.send_error_json(HTTPStatus.CONFLICT, "The live stream audio format cannot be saved.")
                return
            recording_path = folder / f"recording.{extension}"
            with recording_path.open("wb") as recording:
                for row in chunks:
                    with Path(row["file_path"]).open("rb") as chunk:
                        shutil.copyfileobj(chunk, recording)
        with connect_db() as db:
            db.execute(
                """UPDATE streams SET status = 'completed', ended_at = ?, mime_type = ?
                   WHERE id = ? AND status = 'live'""",
                (int(time.time()), recording_mime_type, stream_id),
            )
        self.send_json(
            HTTPStatus.OK,
            {"ok": True, "recording_url": f"/api/recordings/{stream_id}"},
        )

    def do_PUT(self) -> None:
        self.do_POST()

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if re.fullmatch(r"/api/sermon-uploads/[0-9a-f]{32}", path):
                self.cancel_sermon_upload()
            elif path == "/api/church-logo":
                self.delete_church_logo()
            elif path.startswith("/api/media/"):
                self.delete_sermon()
            elif path.startswith("/api/recordings/"):
                self.delete_recording()
            elif path.startswith("/api/verses/"):
                self.delete_church_verse()
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Delete route not found.")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except sqlite3.Error as exc:
            print(f"Delete request failed: {type(exc).__name__}: {exc}")
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "The sermon could not be deleted.")
        except OSError as exc:
            print(f"Media cleanup failed: {type(exc).__name__}: {exc}")
            message = (
                "The church logo was removed from the site, but its file could not be deleted."
                if path == "/api/church-logo"
                else "The sermon was removed from the library, but its files could not be fully removed."
            )
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, message)

    def delete_sermon(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        parts = self.path_parts()
        if len(parts) != 3 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
            return
        sermon_id = parts[2]
        with connect_db() as db:
            sermon = db.execute(
                """SELECT id, original_path, audio_path, cover_path, auto_discovered
                   FROM media_items WHERE id = ? AND kind = 'sermon'""",
                (sermon_id,),
            ).fetchone()
            if not sermon:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Sermon not found.")
                return
            db.execute("DELETE FROM media_items WHERE id = ?", (sermon_id,))
        if sermon["auto_discovered"]:
            for file_path in {sermon["audio_path"], sermon["cover_path"]}:
                if file_path:
                    Path(file_path).unlink(missing_ok=True)
        else:
            folder = MEDIA_DIR / "media" / sermon_id
            if folder.is_dir():
                shutil.rmtree(folder)
        self.send_json(HTTPStatus.OK, {"ok": True, "id": sermon_id})

    def delete_recording(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        parts = self.path_parts()
        if len(parts) != 3 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Recording not found.")
            return
        recording_id = parts[2]
        with connect_db() as db:
            recording = db.execute(
                "SELECT status FROM streams WHERE id = ?", (recording_id,)
            ).fetchone()
            if not recording or recording["status"] != "completed":
                self.send_error_json(HTTPStatus.NOT_FOUND, "Completed sermon recording not found.")
                return
            db.execute("DELETE FROM streams WHERE id = ?", (recording_id,))
        folder = MEDIA_DIR / recording_id
        if folder.is_dir():
            shutil.rmtree(folder)
        self.send_json(HTTPStatus.OK, {"ok": True, "id": recording_id})

    def delete_church_verse(self) -> None:
        admin = self.require_staff(("admin",))
        if not admin:
            return
        parts = self.path_parts()
        if len(parts) != 3 or not UUID_PATTERN.fullmatch(parts[2]):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Church verse not found.")
            return
        with connect_db() as db:
            cursor = db.execute("DELETE FROM church_verses WHERE id = ?", (parts[2],))
        if cursor.rowcount == 0:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Church verse not found.")
            return
        self.send_json(HTTPStatus.OK, {"ok": True, "id": parts[2]})

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/app.js", "/styles.css"):
            file_path = ROOT / ("index.html" if parsed.path in ("/", "/index.html") else parsed.path.lstrip("/"))
            if file_path.is_file():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Length", str(file_path.stat().st_size))
                self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
                self.end_headers()
                return
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    initialize_db()
    host = os.environ.get("LFC_HOST", "127.0.0.1")
    port = int(os.environ.get("LFC_PORT", "8000"))
    server = ChurchServer((host, port), ChurchHandler)
    print(f"Life Fountain Church is running at http://{host}:{port}")
    print(f"Persistent data directory: {DATA_DIR}")
    setup_code_path = DATA_DIR / ".setup-code"
    if setup_code_path.is_file():
        print(f"First-time admin setup code (keep it private): {setup_code_path.read_text(encoding='utf-8').strip()}")
    print("Open the site and sign in or create the first administrator account to begin.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Life Fountain Church.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
