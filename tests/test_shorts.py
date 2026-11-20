
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shorts"))
import deliver
import pipeline

EDITION = pipeline.ROOT / "shorts/editions/2026-11-20.json"


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.edition = json.loads(EDITION.read_text(encoding="utf-8"))

    def test_social_video_is_only_a_signal(self):
        self.edition["sources"][0]["url"] = "https://www.youtube.com/watch?v=example"
        with self.assertRaises(ValueError):
            pipeline.validate(self.edition)

    def test_drops_unverified_edition(self):
        self.edition["confidence"] = "low"
        with self.assertRaises(ValueError):
            pipeline.validate(self.edition)

    def test_rejects_unknown_evidence(self):
        self.edition["scenes"][0][2] = "UNKNOWN"
        with self.assertRaises(ValueError):
            pipeline.validate(self.edition)

    def test_same_event_same_delivery_key(self):
        corrected = copy.deepcopy(self.edition)
        corrected["headline"] += " Уточнение"
        self.assertEqual(pipeline.validate(self.edition), pipeline.validate(corrected))

    def test_package_and_private_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pipeline.metadata(self.edition, pipeline.validate(self.edition), folder)
            self.assertEqual(len(list(folder.iterdir())), 8)
            body = deliver.youtube_metadata(folder)
            self.assertEqual(body["status"], {"privacyStatus": "private"})
            self.assertIn(self.edition["sources"][0]["url"], body["snippet"]["description"])


class DeliveryTests(unittest.TestCase):
    def test_default_dry_run_never_touches_api(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(deliver, "request") as request:
            for channel in ("youtube", "facebook", "telegram"):
                with patch.object(sys, "argv", ["deliver.py", channel, "nonexistent"]), redirect_stdout(io.StringIO()):
                    deliver.main()
            request.assert_not_called()

    def test_public_switch_stops_upload(self):
        with patch.dict("os.environ", {"YOUTUBE_UPLOAD_ENABLED": "true", "YOUTUBE_PUBLISH_ENABLED": "true"},
                        clear=True), patch.object(sys, "argv", ["deliver.py", "youtube", "."]):
            with self.assertRaises(deliver.DeliveryError):
                deliver.main()

    def test_pending_prohibits_second_send(self):
        journal = deliver.Journal.__new__(deliver.Journal)
        journal.path, journal.branch, journal.headers = "/journal", "sw75-journal", {}
        journal.write = Mock()
        with patch.object(deliver, "request", return_value=(200, {}, b'{"content":"eyJzdGF0ZSI6InBlbmRpbmcifQ=="}')):
            with self.assertRaises(deliver.DeliveryError):
                journal.reserve()
        journal.write.assert_not_called()

    def test_api_error_does_not_echo_body(self):
        with self.assertRaisesRegex(deliver.DeliveryError, r"^Facebook HTTP 403 — [^<]+$"):
            deliver.payload((403, {}, b"<private credential>"), "Facebook")

    def test_resumes_existing_private_upload_session(self):
        edition = json.loads(EDITION.read_text(encoding="utf-8"))
        channel = "UC" + "x" * 22
        replies = [
            (200, {}, json.dumps({"items": [{"id": channel}]}).encode()),
            (200, {"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fake"}, b""),
            (503, {}, b""),
            (308, {"Range": "bytes=0-65535"}, b""),
            (201, {}, b'{"id":"test-id"}'),
        ]
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pipeline.metadata(edition, pipeline.validate(edition), folder)
            video = folder / "shorts.mp4"
            video.write_bytes(bytes(250_000))
            journal = Mock()
            with (patch.dict("os.environ", {"YT_CHANNEL_ID": channel}),
                  patch.object(deliver, "refresh", return_value="mock-token"),
                  patch.object(deliver, "request", side_effect=replies) as request,
                  patch.object(deliver.time, "sleep"), redirect_stdout(io.StringIO())):
                deliver.youtube_upload(folder, pipeline.digest(video), journal)
            self.assertEqual(json.loads(request.call_args_list[1].args[3])["status"]["privacyStatus"], "private")
            self.assertEqual(request.call_args_list[3].args[4]["Content-Range"], "bytes */250000")
            self.assertEqual(request.call_args_list[4].args[4]["Content-Range"], "bytes 65536-249999/250000")
            journal.reserve.assert_called_once()
            self.assertEqual(journal.write.call_args.args[0]["id"], "test-id")


if __name__ == "__main__":
    unittest.main()
