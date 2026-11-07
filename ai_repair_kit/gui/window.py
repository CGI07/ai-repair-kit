"""Build and localize the five desktop screens."""

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import __version__
from ..i18n import LANGUAGE_NAMES, LANGUAGE_OPTIONS, Translations
from ..preferences import THEME_OPTIONS, read_choice, save_preferences
from ..tools import codex
from .codex_workflow import CodexWorkflow
from .theme import apply_theme, nav_icons

REPOSITORY_URL = "https://github.com/CGI07/ai-repair-kit"


def label(layout: QVBoxLayout, role: str = "") -> QLabel:
    widget = QLabel()
    widget.setObjectName(role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    layout.addWidget(widget)
    return widget


def panel(layout: QVBoxLayout | QHBoxLayout, role: str = "card") -> QVBoxLayout:
    frame = QFrame()
    frame.setObjectName(role)
    content = QVBoxLayout(frame)
    content.setContentsMargins(18, 16, 18, 16)
    content.setSpacing(8)
    layout.addWidget(frame)
    return content


def buttons(layout: QVBoxLayout, count: int) -> list[QPushButton]:
    row = QHBoxLayout()
    result = [QPushButton() for _ in range(count)]
    for button in result:
        row.addWidget(button)
    row.addStretch()
    layout.addLayout(row)
    return result


class MainWindow(QMainWindow):
    def __init__(
        self, app: QApplication, settings: QSettings, backup_store: Path | None = None
    ) -> None:
        super().__init__()
        self.app = app
        self.settings = settings
        self.translations = Translations(app)
        self.setWindowTitle("AI Repair Kit")
        self.resize(1024, 660)
        self.setMinimumSize(740, 520)

        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(14, 14, 14, 14)
        shell.setSpacing(16)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        shell.addWidget(sidebar)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(9)
        brand = label(side, "brand")
        brand.setText("AI Repair Kit")
        self.preview = label(side, "muted")
        side.addSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setIconSize(QSize(20, 20))
        side.addWidget(self.nav, 1)

        main = QVBoxLayout()
        main.setSpacing(5)
        shell.addLayout(main, 1)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        main.addWidget(self.pages, 1)
        self.status = label(main, "status")
        self.headings: list[tuple[QLabel, QLabel]] = []

        self.build_dashboard(self.add_page())
        self.build_diagnostics(self.add_page())
        self.build_backups(self.add_page())
        self.build_settings(self.add_page())
        self.build_about(self.add_page())
        self.workflow = CodexWorkflow(
            self, self.diagnostics_card, self.backups_card,
            self.scan, self.repair, self.create_backup, self.restore, backup_store,
        )

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.itemSelectionChanged.connect(self.sync_navigation_selection)
        self.nav.setCurrentRow(0)
        self.language.setCurrentIndex(
            LANGUAGE_OPTIONS.index(read_choice(settings, "language", LANGUAGE_OPTIONS, default="en"))
        )
        self.theme.setCurrentIndex(
            THEME_OPTIONS.index(read_choice(settings, "theme", THEME_OPTIONS))
        )
        self.apply_preferences()
        self.language.currentIndexChanged.connect(self.preferences_changed)
        self.theme.currentIndexChanged.connect(self.preferences_changed)
        self.nav.setFocus()

    def sync_navigation_selection(self) -> None:
        selected = self.nav.selectedItems()
        if selected and selected[0] != self.nav.currentItem():
            self.nav.setCurrentItem(selected[0])

    def closeEvent(self, event: QCloseEvent) -> None:
        self.workflow.close()
        super().closeEvent(event)

    def add_page(self) -> QVBoxLayout:
        self.nav.addItem(QListWidgetItem())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        page = QWidget()
        page.setObjectName("page")
        content = QVBoxLayout(page)
        content.setContentsMargins(9, 14, 14, 14)
        content.setSpacing(12)
        self.headings.append((label(content, "pageTitle"), label(content, "muted")))
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return content

    def build_dashboard(self, page: QVBoxLayout) -> None:
        hero = panel(page, "hero")
        self.hero_badge = label(hero, "badge")
        self.hero_badge.setWordWrap(False)
        hero.setAlignment(self.hero_badge, Qt.AlignmentFlag.AlignLeading)
        self.target_title = label(hero, "sectionTitle")
        self.target_name = label(hero)
        self.target_name.setText(codex.DISPLAY_NAME)
        self.target_description = label(hero, "muted")

        cards = QHBoxLayout()
        cards.setSpacing(12)
        page.addLayout(cards)
        diagnostics = panel(cards)
        self.diag_card = label(diagnostics, "sectionTitle")
        self.diag_summary = label(diagnostics, "muted")
        diagnostics.addStretch()
        self.open_diagnostics, = buttons(diagnostics, 1)
        self.open_diagnostics.clicked.connect(lambda: self.nav.setCurrentRow(1))

        backups = panel(cards)
        self.backup_card = label(backups, "sectionTitle")
        self.backup_summary = label(backups, "muted")
        backups.addStretch()
        self.open_backups, = buttons(backups, 1)
        self.open_backups.clicked.connect(lambda: self.nav.setCurrentRow(2))
        page.addStretch()

    def build_diagnostics(self, page: QVBoxLayout) -> None:
        card = panel(page)
        self.diagnostics_card = card
        self.diag_title = label(card, "sectionTitle")
        self.diag_detail = label(card, "muted")
        self.diag_notice = label(card, "notice")
        self.scan, self.repair = buttons(card, 2)
        page.addStretch()

    def build_backups(self, page: QVBoxLayout) -> None:
        card = panel(page)
        self.backups_card = card
        self.backup_title = label(card, "sectionTitle")
        self.backup_detail = label(card, "muted")
        self.backup_notice = label(card, "notice")
        self.create_backup, self.restore = buttons(card, 2)
        page.addStretch()

    def build_settings(self, page: QVBoxLayout) -> None:
        card = panel(page)
        self.language_label = label(card, "sectionTitle")
        self.language_help = label(card, "muted")
        self.language = QComboBox()
        self.language.setObjectName("language")
        self.language.addItems([""] * len(LANGUAGE_OPTIONS))
        card.addWidget(self.language)

        card.addSpacing(10)
        self.theme_label = label(card, "sectionTitle")
        self.theme_help = label(card, "muted")
        self.theme = QComboBox()
        self.theme.setObjectName("theme")
        self.theme.addItems([""] * len(THEME_OPTIONS))
        card.addWidget(self.theme)
        page.addStretch()

    def build_about(self, page: QVBoxLayout) -> None:
        card = panel(page)
        name = label(card, "sectionTitle")
        name.setText("AI Repair Kit")
        self.version = label(card)
        self.license = label(card, "muted")
        self.source, = buttons(card, 1)
        self.source.clicked.connect(self.open_repository)
        page.addStretch()

    def open_repository(self) -> None:
        if not QDesktopServices.openUrl(QUrl(REPOSITORY_URL)):
            self.status.setText(self.tr("Could not open the GitHub repository in your browser."))

    def preferences_changed(self, _index: int) -> None:
        language = LANGUAGE_OPTIONS[self.language.currentIndex()]
        theme = THEME_OPTIONS[self.theme.currentIndex()]
        saved = save_preferences(self.settings, language, theme)
        self.apply_preferences()
        if not saved:
            warning = self.tr("Could not save settings. Your selection applies for this session.")
            self.status.setText(f"{self.status.text()}\n{warning}")

    def apply_preferences(self) -> None:
        loaded = self.translations.apply(LANGUAGE_OPTIONS[self.language.currentIndex()])
        theme = THEME_OPTIONS[self.theme.currentIndex()]
        apply_theme(self.app, theme)
        for row, icon in enumerate(nav_icons(theme, self.devicePixelRatioF())):
            self.nav.item(row).setIcon(icon)
        self.retranslate()
        self.status.setText(self.tr("Local processing · Changes require approval") if loaded else self.tr("Could not load the language file. The app is using English."))

    def retranslate(self) -> None:
        self.preview.setText(self.tr("v1.0 release candidate"))
        self.nav.setAccessibleName(self.tr("Main navigation"))
        pages = (
            (self.tr("Dashboard"), self.tr("Check for local issues and manage backups from approved repairs.")),
            (self.tr("Diagnostics"), self.tr("Check the local configuration and metadata for %1.").replace("%1", codex.DISPLAY_NAME)),
            (self.tr("Backups"), self.tr("Review verified configuration backups for %1.").replace("%1", codex.DISPLAY_NAME)),
            (self.tr("Settings"), self.tr("Choose how the app looks and which language it uses.")),
            (self.tr("About"), self.tr("A free, open-source Windows toolkit for AI development tools.")),
        )
        for row, ((heading, description), (title, intro)) in enumerate(zip(self.headings, pages)):
            self.nav.item(row).setText(title)
            heading.setText(title)
            description.setText(intro)

        self.hero_badge.setText(self.tr("LOCAL DIAGNOSTICS"))
        self.target_title.setText(self.tr("Supported tool"))
        self.target_description.setText(self.tr("Check local %1 configuration and metadata. A repair requires a preview and your approval.").replace("%1", codex.DISPLAY_NAME))
        self.diag_card.setText(pages[1][0])
        self.diag_summary.setText(self.tr("Check configuration, session metadata, and project references."))
        self.open_diagnostics.setText(pages[1][0])
        self.backup_card.setText(pages[2][0])
        self.backup_summary.setText(self.tr("Review backups created by supported repairs and preview a rollback."))
        self.open_backups.setText(pages[2][0])

        self.diag_title.setText(self.tr("Diagnostic checks"))
        self.diag_detail.setText(self.tr("Select a local data folder, then start a read-only check. Conversation files are never opened."))
        self.diag_notice.setText(self.tr("Recovery previews are available only for the supported configuration or Desktop cache cases."))
        self.scan.setText(self.tr("Run diagnostics"))
        self.repair.setText(self.tr("Preview repair"))
        self.backup_title.setText(self.tr("Backup and restore"))
        self.backup_detail.setText(self.tr("Supported repairs create a backup before writing any changes."))
        self.backup_notice.setText(self.tr("Rollback is available only while the repaired file still matches its verified repair output."))
        self.create_backup.setText(self.tr("Refresh backups"))
        self.restore.setText(self.tr("Preview rollback"))

        self.language_label.setText(self.tr("Language"))
        self.language.setAccessibleName(self.language_label.text())
        self.language_help.setText(self.tr("Changes apply immediately. Unsupported Windows languages fall back to English."))
        self.theme_label.setText(self.tr("Theme"))
        self.theme.setAccessibleName(self.theme_label.text())
        self.theme_help.setText(self.tr("Choose a light or dark appearance."))
        self.language.setItemText(0, self.tr("Use Windows language"))
        for index, name in enumerate(LANGUAGE_NAMES.values(), start=1):
            self.language.setItemText(index, name)
        self.theme.setItemText(0, self.tr("Light"))
        self.theme.setItemText(1, self.tr("Dark"))

        self.version.setText(self.tr("Version %1").replace("%1", __version__))
        self.license.setText(self.tr("Released under the GNU General Public License v3.0."))
        self.source.setText(self.tr("Open repository on GitHub"))
        self.workflow.render()
