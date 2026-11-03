from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tidyfiles.categories import (
    CATEGORY_BY_KEY,
    category_for,
    resolve_category,
    selected_categories,
)
from tidyfiles.cli import _configure_output
from tidyfiles.organizer import (
    Operation,
    Organizer,
    OrganizerError,
)
from tidyfiles.paths import human_size, middle_ellipsis, tail_ellipsis
from tidyfiles.settings import load_settings, save_settings


class CategoryTests(unittest.TestCase):
    def test_extension_matching_is_case_insensitive(self) -> None:
        self.assertEqual(category_for("Photo.JPG").key, "images")
        self.assertEqual(category_for("archive.tar.gz").key, "archives")
        self.assertEqual(category_for("main.PY").key, "code")

    def test_known_filename_without_extension(self) -> None:
        self.assertEqual(category_for("Dockerfile").key, "code")
        self.assertEqual(category_for("LICENSE").key, "other")

    def test_unknown_extension_falls_back_to_other(self) -> None:
        self.assertEqual(category_for("notes.xyz").key, "other")
        self.assertEqual(category_for("no_extension").key, "other")

    def test_resolve_by_key_or_folder(self) -> None:
        self.assertEqual(resolve_category("images").key, "images")
        self.assertEqual(resolve_category("文档").key, "documents")
        with self.assertRaises(KeyError):
            resolve_category("not-a-type")
        self.assertEqual(selected_categories(["图片", "code"]), {"images", "code"})


class OrganizerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source"
        self.destination = self.root / "output"
        self.source.mkdir()
        self.organizer = Organizer()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative: str, data: bytes = b"data") -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class ScanTests(OrganizerTestCase):
    def test_scan_classifies_recursively(self) -> None:
        self.write("a.jpg", b"123")
        self.write("sub/b.pdf", b"4567")
        self.write("sub/deep/c.py", b"89")
        self.write("unknown.bin", b"0")

        result = self.organizer.scan(self.source)

        self.assertFalse(result.cancelled)
        self.assertEqual(len(result.files), 4)
        self.assertEqual(result.total_size, 10)
        counts = result.counts()
        self.assertEqual(counts["images"], 1)
        self.assertEqual(counts["documents"], 1)
        self.assertEqual(counts["code"], 1)
        self.assertEqual(counts["other"], 1)

    def test_non_recursive_scan(self) -> None:
        self.write("top.txt")
        self.write("sub/nested.txt")
        result = self.organizer.scan(self.source, recursive=False)
        self.assertEqual([item.relative for item in result.files], [Path("top.txt")])

    def test_destination_is_excluded(self) -> None:
        self.write("keep.jpg")
        output = self.source / "sorted"
        output.mkdir()
        (output / "old.jpg").write_bytes(b"old")
        result = self.organizer.scan(self.source, exclude=[output])
        self.assertEqual([item.relative for item in result.files], [Path("keep.jpg")])

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(OrganizerError):
            self.organizer.scan(self.root / "missing")

    def test_scan_can_be_cancelled(self) -> None:
        self.write("a.jpg")
        event = threading.Event()
        event.set()
        result = self.organizer.scan(self.source, cancel_event=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.files, ())


class PlanTests(OrganizerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.jpg")
        self.write("b.pdf")
        self.write("c.py")
        self.scan = self.organizer.scan(self.source)

    def test_plan_filters_selected_categories(self) -> None:
        plan = self.organizer.build_plan(
            self.scan,
            self.destination,
            selected_categories=["images", "documents"],
        )
        self.assertEqual(len(plan.files), 2)
        self.assertEqual(plan.destination, self.destination.resolve())
        self.assertEqual(
            {item.target_dir.name for item in plan.files}, {"图片", "文档"}
        )

    def test_flat_plan_ignores_category_filter(self) -> None:
        plan = self.organizer.build_plan(
            self.scan,
            self.destination,
            categorized=False,
            selected_categories=["images"],
        )
        self.assertEqual(len(plan.files), 3)
        self.assertTrue(all(item.target_dir == self.destination.resolve() for item in plan.files))

    def test_same_destination_is_rejected(self) -> None:
        with self.assertRaises(OrganizerError):
            self.organizer.build_plan(self.scan, self.source)

    def test_unknown_category_is_rejected(self) -> None:
        with self.assertRaises(OrganizerError):
            self.organizer.build_plan(self.scan, self.destination, selected_categories=["nope"])


class ExecuteTests(OrganizerTestCase):
    def make_plan(self, *, categorized: bool = True, selected=None):
        scan = self.organizer.scan(self.source)
        return self.organizer.build_plan(
            scan,
            self.destination,
            categorized=categorized,
            selected_categories=selected,
        )

    def test_copy_keeps_source_and_categorizes(self) -> None:
        self.write("photo.jpg", b"image")
        self.write("doc/report.pdf", b"pdf")
        plan = self.make_plan()
        result = self.organizer.execute(plan, save_report=False)

        self.assertEqual(result.copied, 2)
        self.assertEqual(result.failed, 0)
        self.assertTrue((self.source / "photo.jpg").exists())
        self.assertEqual((self.destination / "图片" / "photo.jpg").read_bytes(), b"image")
        self.assertEqual((self.destination / "文档" / "report.pdf").read_bytes(), b"pdf")

    def test_move_removes_source(self) -> None:
        self.write("photo.jpg", b"image")
        plan = self.make_plan()
        result = self.organizer.execute(plan, operation=Operation.MOVE, save_report=False)

        self.assertEqual(result.moved, 1)
        self.assertFalse((self.source / "photo.jpg").exists())
        self.assertTrue((self.destination / "图片" / "photo.jpg").exists())

    def test_rename_on_conflict(self) -> None:
        self.write("photo.jpg", b"new")
        target = self.destination / "图片" / "photo.jpg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        plan = self.make_plan()
        result = self.organizer.execute(plan, conflict="rename", save_report=False)

        self.assertEqual(result.completed, 1)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual((target.parent / "photo (1).jpg").read_bytes(), b"new")

    def test_skip_on_conflict(self) -> None:
        self.write("photo.jpg", b"new")
        target = self.destination / "图片" / "photo.jpg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        plan = self.make_plan()
        result = self.organizer.execute(plan, conflict="skip", save_report=False)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertFalse((target.parent / "photo (1).jpg").exists())

    def test_overwrite_on_conflict(self) -> None:
        self.write("photo.jpg", b"new")
        target = self.destination / "图片" / "photo.jpg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        plan = self.make_plan()
        result = self.organizer.execute(plan, conflict="overwrite", save_report=False)

        self.assertEqual(result.completed, 1)
        self.assertEqual(target.read_bytes(), b"new")

    def test_duplicate_names_inside_source_do_not_overwrite_each_other(self) -> None:
        self.write("a/photo.jpg", b"one")
        self.write("b/photo.jpg", b"two")
        plan = self.make_plan()
        result = self.organizer.execute(plan, conflict="overwrite", save_report=False)
        targets = sorted(item.target for item in result.outcomes if item.ok)
        self.assertEqual(len({item.name for item in targets}), 2)
        self.assertEqual({item.read_bytes() for item in targets}, {b"one", b"two"})

    def test_flat_mode(self) -> None:
        self.write("a/photo.jpg")
        self.write("b/song.mp3")
        plan = self.make_plan(categorized=False)
        result = self.organizer.execute(plan, save_report=False)
        self.assertEqual(result.copied, 2)
        self.assertTrue((self.destination / "photo.jpg").exists())
        self.assertTrue((self.destination / "song.mp3").exists())

    def test_unicode_names(self) -> None:
        self.write("照片/旅行 2026.JPG", b"x")
        plan = self.make_plan()
        result = self.organizer.execute(plan, save_report=False)
        self.assertEqual(result.copied, 1)
        self.assertTrue((self.destination / "图片" / "旅行 2026.JPG").exists())

    def test_report_is_json(self) -> None:
        self.write("photo.jpg")
        plan = self.make_plan()
        reports = self.root / "reports"
        result = self.organizer.execute(
            plan,
            conflict="rename",
            reports_directory=reports,
        )
        self.assertIsNotNone(result.report_path)
        self.assertTrue(result.report_path.exists())
        payload = json.loads(result.report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["completed"], 1)
        self.assertEqual(payload["conflict_policy"], "rename")
        self.assertEqual(payload["files"][0]["status"], "copied")

    def test_cancellation_stops_after_current_file(self) -> None:
        for index in range(3):
            self.write(f"file{index}.jpg")
        plan = self.make_plan()
        event = threading.Event()

        def on_progress(_event) -> None:
            event.set()

        result = self.organizer.execute(
            plan,
            progress=on_progress,
            cancel_event=event,
            save_report=False,
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result.outcomes), 1)
        self.assertEqual(len(list((self.destination / "图片").iterdir())), 1)

    def test_missing_source_is_reported_as_failed(self) -> None:
        path = self.write("photo.jpg")
        plan = self.make_plan()
        path.unlink()
        result = self.organizer.execute(plan, save_report=False)
        self.assertEqual(result.failed, 1)
        self.assertFalse((self.destination / "图片" / "photo.jpg").exists())

    def test_output_file_instead_of_directory_is_rejected(self) -> None:
        self.write("photo.jpg")
        self.destination.write_text("not a folder", encoding="utf-8")
        plan = self.make_plan()
        with self.assertRaises(OrganizerError):
            self.organizer.execute(plan, save_report=False)

    def test_invalid_operation_and_conflict(self) -> None:
        self.write("photo.jpg")
        plan = self.make_plan()
        with self.assertRaises(OrganizerError):
            self.organizer.execute(plan, operation="delete", save_report=False)
        with self.assertRaises(OrganizerError):
            self.organizer.execute(plan, conflict="explode", save_report=False)


class UtilityTests(unittest.TestCase):
    def test_human_size(self) -> None:
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1024), "1.00 KB")
        self.assertEqual(human_size(10 * 1024), "10.0 KB")
        self.assertEqual(human_size(150 * 1024), "150 KB")

    def test_middle_ellipsis(self) -> None:
        self.assertEqual(middle_ellipsis("abc", 10), "abc")
        self.assertIn("…", middle_ellipsis("a" * 100, 20))
        self.assertEqual(len(middle_ellipsis("a" * 100, 20)), 20)

    def test_tail_ellipsis(self) -> None:
        self.assertEqual(tail_ellipsis("abc", 10), "abc")
        self.assertTrue(tail_ellipsis("a" * 100 + "end", 20).endswith("end"))
        self.assertEqual(len(tail_ellipsis("a" * 100, 20)), 20)


class CliEncodingTests(unittest.TestCase):
    def test_reconfigures_legacy_stream_to_utf8(self) -> None:
        class FakeStream:
            encoding = "cp1252"
            reconfigured = None

            def reconfigure(self, **kwargs) -> None:
                self.reconfigured = kwargs
                self.encoding = kwargs["encoding"]

        stdout = FakeStream()
        stderr = FakeStream()
        with patch("tidyfiles.cli.sys.stdout", stdout), patch(
            "tidyfiles.cli.sys.stderr", stderr
        ):
            _configure_output()
        self.assertEqual(stdout.encoding, "utf-8")
        self.assertEqual(stderr.reconfigured["errors"], "replace")

    def test_leaves_utf8_stream_untouched(self) -> None:
        class Utf8Stream:
            encoding = "utf-8"

            def reconfigure(self, **_kwargs) -> None:
                raise AssertionError("should not reconfigure UTF-8 output")

        with patch("tidyfiles.cli.sys.stdout", Utf8Stream()), patch(
            "tidyfiles.cli.sys.stderr", Utf8Stream()
        ):
            _configure_output()


class SettingsTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            values = {
                "source": "C:/in",
                "destination": "C:/out",
                "recursive": False,
                "operation": "move",
                "conflict": "skip",
                "categorized": False,
                "selected_categories": ["images", "bad-key"],
                "geometry": "1000x720+10+10",
            }
            self.assertTrue(save_settings(values, path))
            loaded = load_settings(path)
            self.assertEqual(loaded["source"], "C:/in")
            self.assertEqual(loaded["operation"], "move")
            self.assertFalse(loaded["categorized"])
            self.assertEqual(loaded["selected_categories"], ["images"])

    def test_corrupt_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = load_settings(path)
            self.assertTrue(loaded["categorized"])
            self.assertEqual(len(loaded["selected_categories"]), len(CATEGORY_BY_KEY))

    def test_utf8_bom_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                '{"source": "C:/in", "operation": "move"}',
                encoding="utf-8-sig",
            )
            loaded = load_settings(path)
            self.assertEqual(loaded["source"], "C:/in")
            self.assertEqual(loaded["operation"], "move")


if __name__ == "__main__":
    unittest.main()
