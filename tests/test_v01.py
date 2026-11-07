"""Tests for the v0.1 UI and its isolated application preferences."""

import ast
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree

from PySide6.QtCore import QLocale, QSettings, Qt, QTimer, QTranslator
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QPlainTextEdit

from ai_repair_kit.gui import codex_workflow, window as window_module
from ai_repair_kit.gui.theme import COLORS
from ai_repair_kit.gui.window import MainWindow, REPOSITORY_URL
from ai_repair_kit.i18n import CATALOG_DIR, Translations, resolve_language

TEST_WORK = Path(__file__).resolve().parents[1] / ".test-work"


class LocalizationTests(unittest.TestCase):
    def test_system_language_and_english_fallback(self) -> None:
        self.assertEqual(resolve_language("system", QLocale("ko_KR")), "ko")
        self.assertEqual(resolve_language("system", QLocale("de_DE")), "en")
        self.assertEqual(resolve_language("en", QLocale("ko_KR")), "en")
        self.assertEqual(resolve_language("ko", QLocale("en_US")), "ko")
        self.assertEqual(resolve_language("unsupported", QLocale("ko_KR")), "en")

    def test_korean_catalog_covers_ui_strings_and_keeps_placeholders(self) -> None:
        self.assert_catalog("MainWindow", Path(window_module.__file__))
        self.assert_catalog("CodexWorkflow", Path(codex_workflow.__file__))

    def assert_catalog(self, context: str, file: Path) -> None:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        source_strings = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "tr"
        }
        catalog = ElementTree.parse(CATALOG_DIR / "app_ko.ts")
        translator = QTranslator()
        self.assertTrue(translator.load(str(CATALOG_DIR / "app_ko.qm")))
        messages = {
            message.findtext("source"): message.find("translation")
            for group in catalog.findall("context") if group.findtext("name") == context
            for message in group.findall("message")
        }
        self.assertEqual(source_strings, set(messages))
        for source, translation in messages.items():
            with self.subTest(source=source):
                self.assertTrue(translation.text)
                self.assertEqual(translator.translate(context, source), translation.text)
                self.assertIsNone(translation.get("type"))
                self.assertCountEqual(
                    re.findall(r"%[1-9n]", source),
                    re.findall(r"%[1-9n]", translation.text),
                )


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        TEST_WORK.mkdir(exist_ok=True)

    def setUp(self) -> None:
        work = TemporaryDirectory(dir=TEST_WORK)
        self.addCleanup(work.cleanup)
        self.settings = QSettings(str(Path(work.name) / "preferences.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("language", "en")
        self.settings.setValue("theme", "light")
        self.workspace = Path(work.name)
        self.window = MainWindow(self.app, self.settings, self.workspace / "backups")
        self.addCleanup(self.window.close)
        self.addCleanup(self.app.removeTranslator, self.window.translations.translator)
        self.window.show()
        self.app.processEvents()

    def test_five_pages_and_keyboard_navigation(self) -> None:
        nav = self.window.nav
        titles = ["Dashboard", "Diagnostics", "Backups", "Settings", "About"]
        self.assertEqual(nav.count(), len(titles))
        self.assertEqual(self.window.pages.count(), len(titles))
        self.assertEqual(self.window.pages.currentIndex(), 0)
        nav.setFocus()
        for index, title in enumerate(titles):
            if index:
                QTest.keyClick(nav, Qt.Key.Key_Down)
            self.assertEqual(self.window.pages.currentIndex(), index)
            self.assertEqual(nav.currentItem().text(), title)
            self.assertTrue(self.window.headings[index][0].isVisible())

    def test_sidebar_selection_changes_page(self) -> None:
        nav = self.window.nav
        nav.clearSelection()
        nav.item(1).setSelected(True)
        self.assertEqual(nav.currentRow(), 1)
        self.assertEqual(self.window.pages.currentIndex(), 1)

    def test_dashboard_shortcuts(self) -> None:
        QTest.mouseClick(self.window.open_diagnostics, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.window.nav.setCurrentRow(0)
        QTest.mouseClick(self.window.open_backups, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.pages.currentIndex(), 2)

    def test_scan_and_write_actions_start_disabled_without_a_target(self) -> None:
        for button in (self.window.scan, self.window.repair, self.window.restore):
            with self.subTest(button=button.text()):
                self.assertFalse(button.isEnabled())
        self.assertTrue(self.window.create_backup.isEnabled())
        self.assertIn("Desktop cache cases", self.window.diag_notice.text())

    def test_support_report_previews_before_copy_and_omits_profile_path(self) -> None:
        workflow = self.window.workflow
        self.window.nav.setCurrentRow(1)
        self.assertFalse(workflow.support_button.isEnabled())
        workflow.path.setText(str(self.workspace / "private-fixture"))
        workflow.start_scan()
        workflow.future.result(timeout=15)
        workflow.finish_scan()
        self.assertTrue(workflow.support_button.isEnabled())
        clipboard = self.app.clipboard()
        previous = clipboard.text()
        self.addCleanup(clipboard.setText, previous)
        clipboard.setText("unchanged")
        previews = []

        def answer(role: QDialogButtonBox.ButtonRole) -> None:
            dialog = QApplication.activeModalWidget()
            preview = dialog.findChild(QPlainTextEdit)
            previews.append((preview.toPlainText(), preview.isReadOnly()))
            box = dialog.findChild(QDialogButtonBox)
            next(button for button in box.buttons() if box.buttonRole(button) == role).click()

        QTimer.singleShot(0, lambda: answer(QDialogButtonBox.ButtonRole.RejectRole))
        workflow.support_button.click()
        self.assertEqual(clipboard.text(), "unchanged")
        QTimer.singleShot(0, lambda: answer(QDialogButtonBox.ButtonRole.AcceptRole))
        workflow.support_button.click()
        preview, read_only = previews[-1]
        self.assertTrue(read_only)
        self.assertEqual(clipboard.text(), preview)
        self.assertEqual(json.loads(preview)["last_scan"], {"home_missing": 1})
        self.assertNotIn("private-fixture", preview)

    def test_synthetic_scan_cancel_approve_and_rollback_in_the_ui(self) -> None:
        fixture = self.workspace / "codex-fixture"
        fixture.mkdir()
        target = fixture / "config.toml"
        original = b"\0" * 128
        target.write_bytes(original)
        self.window.nav.setCurrentRow(1)
        workflow = self.window.workflow
        workflow.path.setText(str(fixture))
        QTest.mouseClick(self.window.scan, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(workflow.future)
        workflow.future.result(timeout=15)
        workflow.finish_scan()
        self.assertTrue(self.window.repair.isEnabled())
        self.assertIn("NUL bytes", workflow.results.text())

        def answer(choice: QMessageBox.ButtonRole) -> None:
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                next(button for button in dialog.buttons() if dialog.buttonRole(button) == choice).click()

        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.RejectRole))
        QTest.mouseClick(self.window.repair, Qt.MouseButton.LeftButton)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(workflow.store.exists())

        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
        QTest.mouseClick(self.window.repair, Qt.MouseButton.LeftButton)
        self.assertEqual(target.read_bytes(), b"")
        self.assertIn("repaired and verified", workflow.results.text())

        self.window.language.setCurrentIndex(2)
        self.window.nav.setCurrentRow(2)
        self.assertTrue(self.window.restore.isEnabled())
        labels = []

        def approve_in_korean() -> None:
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                approve = next(button for button in dialog.buttons()
                               if dialog.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole)
                labels.extend((dialog.defaultButton().text(), approve.text()))
                labels.append(dialog.text().splitlines()[0])
                answer(QMessageBox.ButtonRole.AcceptRole)
        QTimer.singleShot(0, approve_in_korean)
        QTest.mouseClick(self.window.restore, Qt.MouseButton.LeftButton)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(labels, ["취소", "변경 승인", f"대상: {target}"])
        self.assertIn("원본 바이트를 복원하고 검증했습니다", workflow.backup_status.text())

    def test_startup_uses_english_unless_a_language_was_selected(self) -> None:
        choices = [
            (None, "en", "Dashboard"),
            ("unsupported", "en", "Dashboard"),
            ("en", "en", "Dashboard"),
            ("ko", "ko", "대시보드"),
            ("system", "system", "대시보드"),
        ]
        for theme in ("light", "dark"):
            for saved, expected, title in choices:
                with self.subTest(theme=theme, language=saved):
                    self.settings.remove("language")
                    if saved is not None:
                        self.settings.setValue("language", saved)
                    self.settings.setValue("theme", theme)
                    with patch("ai_repair_kit.i18n.QLocale.system", return_value=QLocale("ko_KR")):
                        reopened = MainWindow(self.app, self.settings, self.workspace / "backups")
                    try:
                        self.assertEqual(
                            window_module.LANGUAGE_OPTIONS[reopened.language.currentIndex()], expected
                        )
                        self.assertEqual(reopened.nav.item(0).text(), title)
                        self.assertEqual(
                            self.app.palette().color(QPalette.ColorRole.Window).name(),
                            COLORS[theme]["canvas"],
                        )
                    finally:
                        reopened.close()
                        self.app.removeTranslator(reopened.translations.translator)

    def test_live_language_change_keeps_current_page(self) -> None:
        self.window.nav.setCurrentRow(3)
        self.window.language.setCurrentIndex(2)
        self.assertEqual(self.window.pages.currentIndex(), 3)
        self.assertEqual(self.window.nav.currentItem().text(), "설정")
        self.assertEqual(self.window.scan.text(), "진단 실행")
        self.assertIn("OpenAI Codex", self.window.headings[1][1].text())
        self.assertNotIn("%1", self.window.headings[1][1].text())
        self.window.language.setCurrentIndex(1)
        self.assertEqual(self.window.nav.currentItem().text(), "Settings")
        self.assertEqual(self.window.scan.text(), "Run diagnostics")

    def test_theme_switch_and_preferences_persist(self) -> None:
        self.window.theme.setCurrentIndex(1)
        self.window.language.setCurrentIndex(2)
        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), COLORS["dark"]["canvas"])
        self.window.nav.setCurrentRow(1)
        self.app.processEvents()
        field = self.window.workflow.path.grab().toImage()
        self.assertEqual(field.pixelColor(field.width() - 10, field.height() // 2).name(),
                         COLORS["dark"]["surface"])
        self.window.close()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        restored = MainWindow(self.app, reopened)
        self.addCleanup(restored.close)
        self.addCleanup(self.app.removeTranslator, restored.translations.translator)
        self.assertEqual(restored.theme.currentIndex(), 1)
        self.assertEqual(restored.nav.item(0).text(), "대시보드")
        restored.theme.setCurrentIndex(0)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window).name(), COLORS["light"]["canvas"]
        )

    def test_missing_catalog_shows_english_and_warning(self) -> None:
        self.window.translations = Translations(self.app, Path(self.settings.fileName()).parent)
        self.window.language.setCurrentIndex(2)
        self.assertEqual(self.window.nav.item(0).text(), "Dashboard")
        self.assertIn("using English", self.window.status.text())

    def test_preference_save_error_is_localized(self) -> None:
        with patch.object(window_module, "save_preferences", return_value=False):
            self.window.language.setCurrentIndex(2)
        self.assertEqual(self.window.nav.item(0).text(), "대시보드")
        self.assertIn("설정을 저장하지 못했습니다", self.window.status.text())

    def test_about_link_uses_expected_url_and_reports_failure(self) -> None:
        self.window.nav.setCurrentRow(4)
        with patch.object(window_module, "QDesktopServices") as desktop:
            desktop.openUrl.return_value = False
            QTest.mouseClick(self.window.source, Qt.MouseButton.LeftButton)
        url, = desktop.openUrl.call_args.args
        self.assertEqual(url.toString(), REPOSITORY_URL)
        self.assertIn("Could not open", self.window.status.text())


if __name__ == "__main__":
    unittest.main()
