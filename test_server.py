import http.cookiejar
import base64
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave
from pathlib import Path

import server


class ChurchServerTests(unittest.TestCase):
    def test_server_uses_render_port_and_public_bind_address(self):
        with patch.dict("os.environ", {"PORT": "10000"}, clear=True):
            self.assertEqual(server.server_bind_address(), ("0.0.0.0", 10000))

    def test_server_keeps_local_bind_defaults_without_render_port(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(server.server_bind_address(), ("127.0.0.1", 8000))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = server.DATA_DIR, server.MEDIA_DIR, server.DATABASE
        server.DATA_DIR = Path(self.temp_dir.name)
        server.MEDIA_DIR = server.DATA_DIR / "media"
        server.DATABASE = server.DATA_DIR / "life-fountain.sqlite3"
        server.initialize_db()
        with server.connect_db() as db:
            db.execute(
                "INSERT INTO studio_credentials(id, password_hash) VALUES (1, ?)",
                (server.password_hash("test-studio-password"),),
            )
        self.httpd = server.ChurchServer(("127.0.0.1", 0), server.ChurchHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.admin_client = self.make_client()
        self.pastor_client = self.make_client()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        server.DATA_DIR, server.MEDIA_DIR, server.DATABASE = self.previous_paths
        self.temp_dir.cleanup()

    @staticmethod
    def make_client():
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, client, path, method="GET", body=None, headers=None):
        data = None
        request_headers = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        else:
            data = body
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=request_headers, method=method
        )
        with client.open(request, timeout=5) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None

    def test_staff_prayer_stream_and_media_workflows(self):
        status, setup = self.request(self.admin_client, "/api/status")
        self.assertTrue(setup["setup_required"])

        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.request(
                self.admin_client,
                "/api/setup",
                "POST",
                {
                    "setup_code": "incorrect",
                    "name": "Church Admin",
                    "email": "admin@example.test",
                    "password": "Amon0619.",
                },
            )
        self.assertEqual(rejected.exception.code, 403)

        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        _, created = self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        self.assertEqual(created["user"]["role"], "admin")

        _, pastor = self.request(
            self.admin_client,
            "/api/pastors",
            "POST",
            {
                "name": "Pastor Lee",
                "email": "pastor@example.test",
                "password": "PastorPass!123",
            },
        )
        public_pastors = self.request(self.make_client(), "/api/pastors/public")[1]
        self.assertEqual(public_pastors[0]["id"], pastor["id"])
        self.assertNotIn("email", public_pastors[0])

        _, prayer = self.request(
            self.make_client(),
            "/api/prayers",
            "POST",
            {
                "name": "Church member",
                "pastor_id": pastor["id"],
                "message": "Please pray for my family.",
            },
        )
        self.assertEqual(prayer["ok"], True)
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            self.request(self.make_client(), "/api/prayers")
        self.assertEqual(forbidden.exception.code, 401)

        self.request(
            self.pastor_client,
            "/api/login",
            "POST",
            {"email": "pastor@example.test", "password": "PastorPass!123"},
        )
        with self.assertRaises(urllib.error.HTTPError) as locked:
            self.request(
                self.pastor_client,
                "/api/streams",
                "POST",
                {"title": "Blocked", "mime_type": "audio/webm;codecs=opus"},
            )
        self.assertEqual(locked.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as wrong_studio_password:
            self.request(
                self.pastor_client,
                "/api/studio/unlock",
                "POST",
                {"password": "incorrect"},
            )
        self.assertEqual(wrong_studio_password.exception.code, 403)
        self.request(
            self.pastor_client,
            "/api/studio/unlock",
            "POST",
            {"password": "test-studio-password"},
        )
        _, assigned = self.request(self.pastor_client, "/api/prayers")
        self.assertEqual(assigned[0]["id"], prayer["id"])
        self.request(
            self.pastor_client,
            f"/api/prayers/{prayer['id']}/response",
            "POST",
            {"response": "We are praying for you."},
        )
        _, answered = self.request(self.admin_client, "/api/prayers")
        self.assertEqual(answered[0]["response"], "We are praying for you.")

        stream_boundary = "life-fountain-stream-boundary"
        stream_cover = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
        )
        stream_body = (
            f"--{stream_boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nSunday service\r\n"
            f"--{stream_boundary}\r\nContent-Disposition: form-data; name=\"mime_type\"\r\n\r\naudio/webm;codecs=opus\r\n"
            f"--{stream_boundary}\r\nContent-Disposition: form-data; name=\"cover\"; filename=\"service.png\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode("utf-8") + stream_cover + f"\r\n--{stream_boundary}--\r\n".encode("ascii")
        _, stream = self.request(
            self.pastor_client,
            "/api/streams",
            "POST",
            stream_body,
            {"Content-Type": f"multipart/form-data; boundary={stream_boundary}"},
        )
        self.assertEqual(stream["title"], "Sunday service")
        self.assertTrue(stream["cover_url"])
        self.assertEqual(
            urllib.request.urlopen(self.base_url + stream["cover_url"], timeout=5).read(),
            stream_cover,
        )
        audio = b"\x1aE\xdf\xa3sample archived audio segment"
        self.request(
            self.pastor_client,
            f"/api/streams/{stream['id']}/chunks",
            "POST",
            audio,
            {"Content-Type": "audio/webm", "X-Audio-Sequence": "0"},
        )
        _, live = self.request(self.make_client(), "/api/stream")
        self.assertEqual(live["stream"]["id"], stream["id"])
        self.assertEqual(live["stream"]["cover_url"], stream["cover_url"])
        _, initial_chunks = self.request(
            self.make_client(), f"/api/streams/{stream['id']}/chunks?after=-1"
        )
        self.assertTrue(initial_chunks["live"])
        self.assertEqual(initial_chunks["sequences"], [0])
        chunk = urllib.request.urlopen(
            self.base_url + f"/api/streams/{stream['id']}/chunks/0", timeout=5
        )
        self.assertEqual(chunk.read(), audio)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as saved_audio:
            saved_audio.setnchannels(1)
            saved_audio.setsampwidth(2)
            saved_audio.setframerate(16000)
            saved_audio.writeframes(b"\x01\x00" * 160)
        saved_audio_data = wav_buffer.getvalue()
        _, saved = self.request(
            self.pastor_client,
            f"/api/streams/{stream['id']}/stop",
            "POST",
            saved_audio_data,
            {"Content-Type": "audio/wav"},
        )
        self.assertIn(stream["id"], saved["recording_url"])
        _, ended_stream_chunks = self.request(
            self.make_client(), f"/api/streams/{stream['id']}/chunks?after=0"
        )
        self.assertFalse(ended_stream_chunks["live"])
        _, recordings = self.request(self.make_client(), "/api/recordings")
        self.assertEqual(recordings[0]["cover_url"], stream["cover_url"])
        _, recording_view = self.request(
            self.make_client(),
            f"/api/recordings/{stream['id']}/view",
            "POST",
            {},
        )
        self.assertEqual(recording_view["view_count"], 1)
        _, recordings_with_view = self.request(self.make_client(), "/api/recordings")
        self.assertEqual(recordings_with_view[0]["view_count"], 1)
        recording = urllib.request.urlopen(self.base_url + saved["recording_url"], timeout=5)
        self.assertEqual(recording.headers.get_content_type(), "audio/wav")
        self.assertEqual(recording.read(), saved_audio_data)
        with self.assertRaises(urllib.error.HTTPError) as pastor_title_edit:
            self.request(
                self.pastor_client,
                f"/api/recordings/{stream['id']}/title",
                "POST",
                {"title": "Pastor changed title"},
            )
        self.assertEqual(pastor_title_edit.exception.code, 403)
        _, renamed_recording = self.request(
            self.admin_client,
            f"/api/recordings/{stream['id']}/title",
            "POST",
            {"title": "Renamed Sunday sermon"},
        )
        self.assertEqual(renamed_recording["title"], "Renamed Sunday sermon")
        _, renamed_recordings = self.request(self.make_client(), "/api/recordings")
        self.assertEqual(renamed_recordings[0]["title"], "Renamed Sunday sermon")
        with self.assertRaises(urllib.error.HTTPError) as pastor_delete:
            self.request(
                self.pastor_client,
                f"/api/recordings/{stream['id']}",
                "DELETE",
            )
        self.assertEqual(pastor_delete.exception.code, 403)
        self.request(self.admin_client, f"/api/recordings/{stream['id']}", "DELETE")
        _, recordings_after_delete = self.request(self.make_client(), "/api/recordings")
        self.assertFalse(any(item["id"] == stream["id"] for item in recordings_after_delete))

        boundary = "life-fountain-test-boundary"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nshort\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nWelcome video\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"welcome.mp4\"\r\nContent-Type: video/mp4\r\n\r\n",
        ]
        multipart = "".join(parts).encode("utf-8") + b"fake video bytes\r\n" + f"--{boundary}--\r\n".encode("ascii")
        _, media = self.request(
            self.pastor_client,
            "/api/media",
            "POST",
            multipart,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assertEqual(media["title"], "Welcome video")
        self.assertEqual(urllib.request.urlopen(self.base_url + media["video_url"], timeout=5).read(), b"fake video bytes")

    def test_sermon_conversion_keeps_wallpaper_and_discards_video_source(self):
        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        boundary = "life-fountain-sermon-boundary"
        cover = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
        )
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nsermon\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nSunday message\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sermon.mp4\"\r\nContent-Type: video/mp4\r\n\r\n"
        ).encode("utf-8") + b"video source" + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"cover\"; filename=\"wallpaper.png\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode("utf-8") + cover + f"\r\n--{boundary}--\r\n".encode("ascii")

        def fake_ffmpeg(command, **_kwargs):
            Path(command[-1]).write_bytes(b"ID3" + b"converted audio")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("server.shutil.which", return_value="ffmpeg"), patch(
            "server.subprocess.run", side_effect=fake_ffmpeg
        ):
            _, sermon = self.request(
                self.admin_client,
                "/api/media",
                "POST",
                body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

        self.assertEqual(sermon["kind"], "sermon")
        self.assertTrue(sermon["audio_url"])
        self.assertTrue(sermon["cover_url"])
        _, items = self.request(self.make_client(), "/api/media")
        stored = next(item for item in items if item["id"] == sermon["id"])
        self.assertEqual(stored["title"], "Sunday message")
        self.assertTrue(stored["cover_url"])
        with server.connect_db() as db:
            row = db.execute(
                "SELECT original_path, audio_path, cover_path FROM media_items WHERE id = ?",
                (sermon["id"],),
            ).fetchone()
        self.assertEqual(row["original_path"], "")
        self.assertFalse((Path(row["audio_path"]).parent / "original.mp4").exists())
        self.assertTrue(Path(row["audio_path"]).is_file())
        self.assertTrue(Path(row["cover_path"]).is_file())
        with urllib.request.urlopen(self.base_url + sermon["audio_url"], timeout=5) as response:
            self.assertEqual(response.read(), b"ID3converted audio")
        with urllib.request.urlopen(self.base_url + sermon["cover_url"], timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), cover)
        media_folder = Path(row["audio_path"]).parent
        self.request(self.admin_client, f"/api/media/{sermon['id']}", "DELETE")
        with server.connect_db() as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM media_items WHERE id = ?", (sermon["id"],)
            ).fetchone())
        self.assertFalse(media_folder.exists())

    def test_church_profile_and_daily_verse_admin_workflows(self):
        _, profile = self.request(self.make_client(), "/api/church-profile")
        self.assertEqual(profile["name"], "Life Fountain Church")
        self.assertIsNone(profile["latitude"])
        self.assertEqual(self.request(self.make_client(), "/api/verses")[1], [])

        with self.assertRaises(urllib.error.HTTPError) as anonymous_profile_update:
            self.request(
                self.make_client(),
                "/api/church-profile",
                "POST",
                {"name": "Unauthorized"},
            )
        self.assertEqual(anonymous_profile_update.exception.code, 401)

        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        profile_update = {
            "name": "Life Fountain Church",
            "leader": "Pastor Lee",
            "phone": "+123456789",
            "email": "hello@example.test",
            "address": "123 Church Road",
            "about": "A place to worship and grow.",
            "latitude": -1.2864,
            "longitude": 36.8172,
        }
        self.request(self.admin_client, "/api/church-profile", "POST", profile_update)
        _, updated_profile = self.request(self.make_client(), "/api/church-profile")
        self.assertEqual(updated_profile["leader"], "Pastor Lee")
        self.assertEqual(updated_profile["latitude"], -1.2864)
        self.assertEqual(updated_profile["longitude"], 36.8172)
        with self.assertRaises(urllib.error.HTTPError) as incomplete_coordinates:
            self.request(
                self.admin_client,
                "/api/church-profile",
                "POST",
                {**profile_update, "longitude": None},
            )
        self.assertEqual(incomplete_coordinates.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as non_finite_coordinates:
            self.request(
                self.admin_client,
                "/api/church-profile",
                "POST",
                {**profile_update, "latitude": float("nan")},
            )
        self.assertEqual(non_finite_coordinates.exception.code, 400)

        _, verse = self.request(
            self.admin_client,
            "/api/verses",
            "POST",
            {"text": "The Lord is my shepherd.", "reference": "Psalm 23:1"},
        )
        self.assertEqual(verse["reference"], "Psalm 23:1")
        _, public_verses = self.request(self.make_client(), "/api/verses")
        self.assertEqual(public_verses[0]["id"], verse["id"])

        _, pastor = self.request(
            self.admin_client,
            "/api/pastors",
            "POST",
            {
                "name": "Pastor Lee",
                "email": "pastor@example.test",
                "password": "PastorPass!123",
            },
        )
        self.request(
            self.pastor_client,
            "/api/login",
            "POST",
            {"email": "pastor@example.test", "password": "PastorPass!123"},
        )
        with self.assertRaises(urllib.error.HTTPError) as pastor_profile_update:
            self.request(self.pastor_client, "/api/church-profile", "POST", profile_update)
        self.assertEqual(pastor_profile_update.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as pastor_add_verse:
            self.request(
                self.pastor_client,
                "/api/verses",
                "POST",
                {"text": "A verse.", "reference": "Psalm 1:1"},
            )
        self.assertEqual(pastor_add_verse.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as pastor_delete_verse:
            self.request(self.pastor_client, f"/api/verses/{verse['id']}", "DELETE")
        self.assertEqual(pastor_delete_verse.exception.code, 403)
        self.request(self.admin_client, f"/api/verses/{verse['id']}", "DELETE")
        self.assertEqual(self.request(self.make_client(), "/api/verses")[1], [])

    def test_logo_announcements_audio_sermons_and_view_counts(self):
        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        _, pastor = self.request(
            self.admin_client,
            "/api/pastors",
            "POST",
            {
                "name": "Pastor Lee",
                "email": "pastor@example.test",
                "password": "PastorPass!123",
            },
        )
        self.request(
            self.pastor_client,
            "/api/login",
            "POST",
            {"email": "pastor@example.test", "password": "PastorPass!123"},
        )
        with self.assertRaises(urllib.error.HTTPError) as anonymous_logo:
            self.request(self.make_client(), "/api/church-logo", "POST", b"")
        self.assertEqual(anonymous_logo.exception.code, 401)

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
        )
        logo_boundary = "lfc-logo-test"
        logo_body = (
            f"--{logo_boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"logo.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + png + f"\r\n--{logo_boundary}--\r\n".encode()
        _, logo = self.request(
            self.admin_client,
            "/api/church-logo",
            "POST",
            logo_body,
            {"Content-Type": f"multipart/form-data; boundary={logo_boundary}"},
        )
        _, profile = self.request(self.make_client(), "/api/church-profile")
        self.assertEqual(profile["logo_url"], logo["logo_url"])
        with urllib.request.urlopen(self.base_url + logo["logo_url"], timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), png)

        _, everyone_announcement = self.request(
            self.pastor_client,
            "/api/announcements",
            "POST",
            {"title": "Sunday service", "message": "Service begins at 10.", "audience": "everyone"},
        )
        _, pastor_announcement = self.request(
            self.pastor_client,
            "/api/announcements",
            "POST",
            {"title": "Team meeting", "message": "Pastors meet at 9.", "audience": "pastors"},
        )
        _, public_announcements = self.request(self.make_client(), "/api/announcements")
        self.assertEqual([item["id"] for item in public_announcements], [everyone_announcement["id"]])
        _, staff_announcements = self.request(self.admin_client, "/api/announcements")
        self.assertEqual(
            {item["id"] for item in staff_announcements},
            {everyone_announcement["id"], pastor_announcement["id"]},
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_audience:
            self.request(
                self.admin_client,
                "/api/announcements",
                "POST",
                {"title": "Invalid", "message": "Message", "audience": "members"},
            )
        self.assertEqual(invalid_audience.exception.code, 400)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(16000)
            audio_file.writeframes(b"\x01\x00" * 160)
        audio_data = wav_buffer.getvalue()
        audio_boundary = "lfc-audio-sermon-test"
        audio_body = (
            f"--{audio_boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\naudio-sermon\r\n"
            f"--{audio_boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nPrayer and hope\r\n"
            f"--{audio_boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"prayer.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + audio_data + f"\r\n--{audio_boundary}--\r\n".encode()
        _, sermon = self.request(
            self.pastor_client,
            "/api/media",
            "POST",
            audio_body,
            {"Content-Type": f"multipart/form-data; boundary={audio_boundary}"},
        )
        self.assertEqual(sermon["kind"], "sermon")
        with urllib.request.urlopen(self.base_url + sermon["audio_url"], timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "audio/wav")
            self.assertEqual(response.read(), audio_data)
        _, initial_media = self.request(self.make_client(), "/api/media")
        initial_sermon = next(item for item in initial_media if item["id"] == sermon["id"])
        self.assertEqual(initial_sermon["view_count"], 0)
        _, counted = self.request(
            self.make_client(), initial_sermon["view_url"], "POST", {}
        )
        self.assertEqual(counted["view_count"], 1)
        _, refreshed_media = self.request(self.make_client(), "/api/media")
        self.assertEqual(
            next(item for item in refreshed_media if item["id"] == sermon["id"])["view_count"],
            1,
        )

        with self.assertRaises(urllib.error.HTTPError) as pastor_logo_delete:
            self.request(self.pastor_client, "/api/church-logo", "DELETE")
        self.assertEqual(pastor_logo_delete.exception.code, 403)
        self.request(self.admin_client, "/api/church-logo", "DELETE")
        _, cleared_profile = self.request(self.make_client(), "/api/church-profile")
        self.assertIsNone(cleared_profile["logo_url"])
        self.assertEqual(pastor["role"], "pastor")

    def test_auto_discovers_stable_complete_sermon_files(self):
        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        import_folder = server.MEDIA_DIR / "sermon-imports"
        import_folder.mkdir()
        audio_path = import_folder / "Sunday_service.wav"
        with wave.open(str(audio_path), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(16000)
            audio_file.writeframes(b"\x01\x00" * 160)
        audio_data = audio_path.read_bytes()
        cover_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
        )
        cover_path = import_folder / "cover.png"
        cover_path.write_bytes(cover_data)
        (import_folder / "unfinished.wav.tmp").write_bytes(audio_data)
        (import_folder / "00000001.part").write_bytes(b"partial live audio")

        _, before_stable = self.request(self.make_client(), "/api/media")
        self.assertFalse(any(item["title"] == "Sunday service" for item in before_stable))
        time.sleep(server.MEDIA_DISCOVERY_STABILITY_SECONDS + 0.1)

        _, discovered_items = self.request(self.make_client(), "/api/media")
        discovered = next(item for item in discovered_items if item["title"] == "Sunday service")
        self.assertEqual(discovered["speaker"], "Life Fountain Church")
        self.assertEqual(discovered["view_count"], 0)
        self.assertTrue(discovered["audio_url"])
        self.assertTrue(discovered["cover_url"])
        with urllib.request.urlopen(self.base_url + discovered["audio_url"], timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "audio/wav")
            self.assertEqual(response.read(), audio_data)
        with urllib.request.urlopen(self.base_url + discovered["cover_url"], timeout=5) as response:
            self.assertEqual(response.read(), cover_data)
        with server.connect_db() as db:
            db.execute(
                "UPDATE media_items SET cover_path = NULL WHERE id = ?",
                (discovered["id"],),
            )
        _, refreshed_cover_items = self.request(self.make_client(), "/api/media")
        restored_cover = next(item for item in refreshed_cover_items if item["id"] == discovered["id"])
        self.assertTrue(restored_cover["cover_url"])
        with urllib.request.urlopen(self.base_url + restored_cover["cover_url"], timeout=5) as response:
            self.assertEqual(response.read(), cover_data)
        _, renamed = self.request(
            self.admin_client,
            f"/api/media/{discovered['id']}/title",
            "POST",
            {"title": "A New Sunday Message"},
        )
        self.assertEqual(renamed["title"], "A New Sunday Message")
        _, renamed_items = self.request(self.make_client(), "/api/media")
        self.assertTrue(any(item["title"] == "A New Sunday Message" for item in renamed_items))
        _, after_duplicate_scan = self.request(self.make_client(), "/api/media")
        self.assertEqual(len([item for item in after_duplicate_scan if item["id"] == discovered["id"]]), 1)

        self.request(self.admin_client, f"/api/media/{discovered['id']}", "DELETE")
        self.assertFalse(audio_path.exists())
        self.assertFalse(cover_path.exists())

    def test_large_video_upload_uses_chunks_and_accepts_files_over_five_gb(self):
        setup_code = (server.DATA_DIR / ".setup-code").read_text(encoding="utf-8")
        self.request(
            self.admin_client,
            "/api/setup",
            "POST",
            {
                "setup_code": setup_code,
                "name": "Church Admin",
                "email": "admin@example.test",
                "password": "Amon0619.",
            },
        )
        _, large_upload = self.request(
            self.admin_client,
            "/api/sermon-uploads",
            "POST",
            {
                "filename": "long-service.mp4",
                "mime_type": "video/mp4",
                "file_size": 6 * 1024 * 1024 * 1024,
                "title": "Long service",
            },
        )
        self.assertEqual(large_upload["file_size"], 6 * 1024 * 1024 * 1024)
        _, upload_status = self.request(
            self.admin_client,
            f"/api/sermon-uploads/{large_upload['id']}",
        )
        self.assertEqual(upload_status["uploaded_bytes"], 0)
        self.request(
            self.admin_client,
            f"/api/sermon-uploads/{large_upload['id']}",
            "DELETE",
        )

        original_chunk_size = server.SERMON_UPLOAD_CHUNK_BYTES
        server.SERMON_UPLOAD_CHUNK_BYTES = 32
        try:
            video_bytes = b"sample video payload that is transferred in more than one chunk"
            _, upload = self.request(
                self.admin_client,
                "/api/sermon-uploads",
                "POST",
                {
                    "filename": "service.mp4",
                    "mime_type": "video/mp4",
                    "file_size": len(video_bytes),
                    "title": "Sunday service recording",
                },
            )
            for offset in range(0, len(video_bytes), upload["chunk_size"]):
                chunk = video_bytes[offset:offset + upload["chunk_size"]]
                self.request(
                    self.admin_client,
                    f"/api/sermon-uploads/{upload['id']}/chunk",
                    "POST",
                    chunk,
                    {
                        "Content-Type": "application/octet-stream",
                        "X-Upload-Offset": str(offset),
                    },
                )
            _, complete_status = self.request(
                self.admin_client,
                f"/api/sermon-uploads/{upload['id']}",
            )
            self.assertEqual(complete_status["uploaded_bytes"], len(video_bytes))

            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
            )
            boundary = "large-sermon-cover-boundary"
            cover_body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"wallpaper.png\"\r\n"
                "Content-Type: image/png\r\n\r\n"
            ).encode() + png + f"\r\n--{boundary}--\r\n".encode()
            self.request(
                self.admin_client,
                f"/api/sermon-uploads/{upload['id']}/cover",
                "POST",
                cover_body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"ID3converted sermon")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("server.shutil.which", return_value="ffmpeg"), patch(
                "server.subprocess.run", side_effect=fake_ffmpeg
            ):
                _, sermon = self.request(
                    self.admin_client,
                    f"/api/sermon-uploads/{upload['id']}/complete",
                    "POST",
                    {},
                )
            self.assertEqual(sermon["title"], "Sunday service recording")
            with urllib.request.urlopen(self.base_url + sermon["audio_url"], timeout=5) as response:
                self.assertEqual(response.read(), b"ID3converted sermon")
            with urllib.request.urlopen(self.base_url + sermon["cover_url"], timeout=5) as response:
                self.assertEqual(response.read(), png)
            self.request(self.admin_client, f"/api/media/{sermon['id']}", "DELETE")
        finally:
            server.SERMON_UPLOAD_CHUNK_BYTES = original_chunk_size

    def test_kjv_dataset_contains_all_books_and_chapters(self):
        with (server.ROOT / "bible-kjv.json").open(encoding="utf-8") as bible_file:
            books = json.load(bible_file)
        self.assertEqual(len(books), 66)
        self.assertEqual(sum(len(book["chapters"]) for book in books), 1189)
        self.assertEqual(
            sum(len(chapter["verses"]) for book in books for chapter in book["chapters"]),
            31102,
        )
        self.assertTrue(all(chapter["verses"] for book in books for chapter in book["chapters"]))
        with urllib.request.urlopen(self.base_url + "/bible-kjv.json", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            self.assertEqual(response.headers.get("Cache-Control"), "public, max-age=86400")
            self.assertEqual(len(response.read()), (server.ROOT / "bible-kjv.json").stat().st_size)


if __name__ == "__main__":
    unittest.main()
