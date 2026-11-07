"""Connect the existing Qt views to the local Codex services."""

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..tools import codex, codex_diagnostics, codex_repair, desktop_repair, support_report
from ..tools.codex_diagnostics import Code, Finding, Report
from ..tools.desktop_catalog import canonical_id
from ..tools.desktop_thread import (
    GoalComparison, GoalStatus, Presence, ProjectComparison, ProjectLink, RolloutReference,
    ThreadLocationChanged, ThreadReport, inspect_scanned_thread,
)


class CodexWorkflow(QObject):
    def __init__(
        self, parent: QWidget, diagnostics: QVBoxLayout, backups: QVBoxLayout,
        scan: QPushButton, repair: QPushButton, refresh: QPushButton, restore: QPushButton,
        store: Path | None,
    ) -> None:
        super().__init__(parent)
        self.window = parent
        self.store = store or Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        ) / "recovery-backups"
        self.scan, self.repair = scan, repair
        self.refresh, self.restore = refresh, restore
        self.locations: codex.Locations | None = None
        self.report: Report | None = None
        self.message = ""
        self.backup_count: int | None = None
        self.backup_error = False
        self.show_backup_outcome = False
        self.future: Future[Report] | Future[ThreadReport] | None = None
        self.thread_report: ThreadReport | None = None
        self.invalid_thread = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.finish_scan)

        self.path = QLineEdit()
        self.path.setObjectName("codexHome")
        self.detect_button, self.browse_button = QPushButton(), QPushButton()
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_button)
        diagnostics.insertWidget(2, self.detect_button)
        diagnostics.insertLayout(3, row)
        self.location_note = self.new_note()
        diagnostics.insertWidget(4, self.location_note)
        self.results = self.new_note()
        diagnostics.addWidget(self.results)
        self.desktop_repair = QPushButton()
        self.desktop_history = QPushButton()
        diagnostics.addWidget(self.desktop_repair)
        diagnostics.addWidget(self.desktop_history)
        self.thread_button = QPushButton()
        self.thread_button.setObjectName("checkCodexThread")
        self.thread_button.clicked.connect(self.start_thread_check)
        diagnostics.addWidget(self.thread_button)
        self.thread_status = self.new_note()
        diagnostics.addWidget(self.thread_status)
        self.support_button = QPushButton()
        self.support_button.setObjectName("reviewSupportReport")
        self.support_button.clicked.connect(self.review_support_report)
        diagnostics.addWidget(self.support_button)
        self.desktop_repair.clicked.connect(lambda: self.change_desktop(False))
        self.desktop_history.clicked.connect(lambda: self.change_desktop(True))

        self.backups = QComboBox()
        self.backups.setObjectName("recoveryBackups")
        backups.insertWidget(3, self.backups)
        self.backup_status = self.new_note()
        backups.addWidget(self.backup_status)

        self.detect_button.clicked.connect(self.detect)
        self.browse_button.clicked.connect(self.browse)
        self.path.textChanged.connect(self.path_changed)
        self.scan.clicked.connect(self.start_scan)
        self.repair.clicked.connect(self.repair_config)
        self.refresh.clicked.connect(self.refresh_backups)
        self.restore.clicked.connect(self.restore_config)
        self.backups.currentIndexChanged.connect(self.update_controls)

    def new_note(self) -> QLabel:
        result = QLabel()
        result.setWordWrap(True)
        result.setTextFormat(Qt.TextFormat.PlainText)
        result.setObjectName("muted")
        return result

    def close(self) -> None:
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def detect(self) -> None:
        self.locations = codex.discover()
        self.path.setText(str(self.locations.home))
        self.render()

    def browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self.window, self.tr("Choose the Codex data folder"))
        if directory:
            self.path.setText(directory)

    def path_changed(self) -> None:
        self.thread_report = None
        self.invalid_thread = False
        self.report = None
        self.message = ""
        self.show_backup_outcome = False
        self.backup_count = None
        self.backup_error = False
        self.backups.clear()
        self.render()

    def start_scan(self) -> None:
        if not self.path.text().strip() or self.future is not None:
            return
        home = Path(self.path.text().strip())
        locations = self.locations
        override = locations.sqlite_override if locations and locations.home == home else None
        self.report = None
        self.thread_report = None
        self.invalid_thread = False
        self.message = "waiting"
        self.show_backup_outcome = False
        self.future = self.executor.submit(codex_diagnostics.inspect, home, override)
        self.timer.start()
        self.render()

    def start_thread_check(self) -> None:
        if self.future is not None or not self.report or not self.report.db_home:
            return
        report = self.report
        home = Path(self.path.text().strip())
        text, accepted = QInputDialog.getText(self.window, self.tr("Check thread metadata"), self.tr("Enter a thread UUID or its Codex deep link to check the thread, Goal records and session filenames."))
        if not accepted or self.report is not report:
            return
        key = text.strip().lower().removeprefix("codex://threads/")
        self.thread_report = None
        self.invalid_thread = not canonical_id(key)
        if not self.invalid_thread:
            self.show_backup_outcome = False
            self.message = "waiting"
            self.future = self.executor.submit(inspect_scanned_thread, home, report, key)
            self.timer.start()
        self.render()

    def finish_scan(self) -> None:
        if self.future is None or not self.future.done():
            return
        self.timer.stop()
        try:
            result = self.future.result()
            if isinstance(result, ThreadReport):
                self.thread_report = result
            else:
                self.report = result
            self.message = ""
        except ThreadLocationChanged:
            self.report = None
            self.thread_report = None
            self.message = "thread_location_changed"
        except (OSError, ValueError):
            self.message = "scan_error"
        finally:
            self.future = None
            self.render()

    def refresh_backups(self, *, select: str | None = None) -> None:
        self.backups.clear()
        self.backup_error = False
        try:
            entries = codex_repair.list_backups(self.store)
            selected = self.path.text().strip()
            desktop_entries = desktop_repair.list_backups(self.store, home=Path(selected)) if selected else []
        except (OSError, ValueError):
            entries = []
            desktop_entries = []
            self.backup_error = True
        for key in desktop_entries:
            self.backups.addItem(self.tr("Desktop cache · %1").replace("%1", key), ("desktop", key))
        if select in desktop_entries:
            self.backups.setCurrentIndex(desktop_entries.index(select))
        for item in entries:
            self.backups.addItem(item.id, item.id)
        self.backup_count = len(entries) + len(desktop_entries)
        self.render()

    def repair_config(self) -> None:
        if self.report and self.report.null_config_size:
            self.change_config(None)

    def restore_config(self) -> None:
        if self.path.text().strip() and self.backups.currentData():
            item = self.backups.currentData()
            if isinstance(item, (tuple, list)):
                self.change_desktop(False, item[1])
            else:
                self.change_config(item)

    def change_desktop(self, historical: bool, backup_id: str | None = None) -> None:
        home = Path(self.path.text().strip())
        self.show_backup_outcome = backup_id is not None
        try:
            if backup_id:
                restore_plan = desktop_repair.prepare_restore(home, self.store, backup_id)
                if restore_plan.current_hash is None:
                    preview = self.tr("Target: %1\nBackup ID: %2\nRestore %3 bytes (SHA-256: %4) from the verified displaced original. The current cache is missing. Any file that appears will be preserved.")
                else:
                    preview = self.tr("Target: %1\nBackup ID: %2\nRestore %3 bytes (SHA-256: %4). The current cache will be backed up and verified before rollback.")
                preview = preview.replace("%2", restore_plan.backup_id).replace("%3", str(restore_plan.size))
                preview = preview.replace("%4", restore_plan.original_hash)
                preview = preview.replace("%1", str(home / desktop_repair.CACHE_NAME))
                if not self.confirm_change(self.tr("Roll back the Desktop cache"), preview):
                    return
                new_backup = desktop_repair.restore(restore_plan, self.store, approved=True)
                self.message = "desktop_restored"
            else:
                if not self.report or not self.report.db_home:
                    return
                builds = " / ".join(desktop_repair.HISTORY_BUILDS if historical else desktop_repair.REGISTRY_BUILDS)
                version, entered = QInputDialog.getText(
                    self.window,
                    self.tr("Installed Codex Desktop package version"),
                    self.tr("Enter the installed Windows package (MSIX) version. This recovery rule supports %1.").replace("%1", builds),
                )
                if not entered:
                    return
                history = None
                if historical:
                    patterns = " ".join(desktop_repair.HISTORY_FILENAMES)
                    label = self.tr("Desktop cache snapshots")
                    path, _ = QFileDialog.getOpenFileName(
                        self.window, self.tr("Choose a known-good pre-incident Desktop JSON snapshot"),
                        "", f"{label} ({patterns})",
                    )
                    if not path:
                        return
                    history = Path(path)
                plan = desktop_repair.prepare(
                    home, self.report.db_home, version.strip(), history,
                    sqlite_override=self.report.sqlite_override,
                )
                if plan.build == desktop_repair.TIMESTAMP_BUILD:
                    preview = self.tr("Target: %1\nProjects with missing timestamps to update: %2\nOnly missing createdAt and updatedAt fields will be filled from matching SQLite records. Chat links and existing fields will be preserved. The full cache will be backed up and verified first. Check Codex after restart.")
                else:
                    preview = self.tr("Target: %1\nProjects to add: %2\nHistorical chat links to add: %3\nHistorical links left untouched: %4\nOnly the Desktop cache will change. The full cache will be backed up and verified first. Check Codex after restart.")
                for placeholder, value in (("%2", plan.count), ("%3", plan.linked), ("%4", plan.skipped)):
                    preview = preview.replace(placeholder, str(value))
                preview = preview.replace("%1", str(home / desktop_repair.CACHE_NAME))
                if not self.confirm_change(self.tr("Recover Desktop projects and links"), preview):
                    return
                new_backup = desktop_repair.apply(plan, self.store, approved=True)
                self.message = "desktop_repaired"
            self.report = None
            self.refresh_backups(select=new_backup)
        except codex_repair.RepairError as error:
            self.message = str(error)
        except (OSError, ValueError):
            self.message = "io_error"
        self.render()

    def change_config(self, backup_id: str | None) -> None:
        home = Path(self.path.text().strip())
        self.show_backup_outcome = bool(backup_id)
        try:
            if backup_id:
                plan = codex_repair.prepare_restore(home, self.store, backup_id)
                title = self.tr("Restore the previous damaged file")
                preview = self.tr("Target: %1\nRestore %2 original NUL bytes from the selected backup. This restores the original damage and may prevent Codex from loading.")
                size = plan.backup.size
            else:
                plan = codex_repair.prepare_repair(home)
                title = self.tr("Repair configuration")
                preview = self.tr("Target: %1\nReplace %2 NUL bytes with an empty TOML file. The original bytes will be backed up and verified first. Codex can then use its defaults for this user configuration layer.")
                size = plan.target.size
            if not self.confirm_change(title, preview.replace("%2", str(size)).replace("%1", str(plan.target.path))):
                return
            codex_repair.apply(plan, self.store, approved=True)
            self.report = None
            self.message = "restored" if backup_id else "repaired"
            self.refresh_backups()
        except codex_repair.RepairError as error:
            self.report = None
            self.message = str(error)
        except (OSError, ValueError):
            self.report = None
            self.message = "io_error"
        self.render()

    def confirm_change(self, title: str, preview: str) -> bool:
        dialog = QMessageBox(self.window)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(title)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(preview)
        dialog.setInformativeText(self.tr("Close Codex, its terminals, and IDE extensions before approving. Keep them closed until the operation finishes."))
        approve = dialog.addButton(self.tr("Approve change"), QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        accepted = dialog.clickedButton() is approve
        dialog.deleteLater()
        return accepted

    def review_support_report(self) -> None:
        if self.future is not None or not (self.report or self.message in support_report.OUTCOMES):
            return
        report = support_report.format_report(self.report, self.thread_report, self.message)
        dialog = QDialog(self.window)
        dialog.setWindowTitle(self.tr("Review support report"))
        dialog.resize(520, 370)
        layout = QVBoxLayout(dialog)
        note = self.new_note()
        note.setText(self.tr("Review the codes and counts before sharing. The app does not send this report. Do not attach original logs, profiles, or backups."))
        layout.addWidget(note)
        preview = QPlainTextEdit(report)
        preview.setAccessibleName(self.tr("Support report preview"))
        preview.setReadOnly(True)
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox()
        buttons.addButton(self.tr("Copy report"), QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton(self.tr("Close report"), QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QApplication.clipboard().setText(report)
        dialog.deleteLater()

    def update_controls(self) -> None:
        idle = self.future is None
        has_path = bool(self.path.text().strip())
        self.path.setEnabled(idle)
        self.detect_button.setEnabled(idle)
        self.browse_button.setEnabled(idle)
        self.scan.setEnabled(idle and has_path)
        self.repair.setEnabled(idle and bool(self.report and self.report.null_config_size))
        self.restore.setEnabled(idle and has_path and self.backups.currentIndex() >= 0)
        codes = {item.code for item in self.report.findings} if self.report else set()
        can_preview = Code.DB_WAL_DIAGNOSTIC not in codes
        self.desktop_repair.setVisible(can_preview and bool(codes & {
            Code.DESKTOP_CACHE_EMPTY, Code.DESKTOP_PROJECT_TIMESTAMPS_MISSING,
        }))
        pending_empty_cache = {Code.DESKTOP_CACHE_EMPTY, Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING} <= codes
        self.desktop_history.setVisible(Code.DESKTOP_PROJECTS in codes and (can_preview or pending_empty_cache))
        self.desktop_repair.setEnabled(idle)
        self.desktop_history.setEnabled(idle)
        can_check_thread = bool(self.report and self.report.db_home)
        self.thread_button.setVisible(can_check_thread)
        self.thread_button.setEnabled(idle and can_check_thread)
        self.support_button.setEnabled(idle and bool(self.report or self.message in support_report.OUTCOMES))

    def render(self) -> None:
        self.update_controls()
        self.path.setPlaceholderText(self.tr("Codex data folder"))
        self.path.setAccessibleName(self.tr("Codex data folder"))
        self.detect_button.setText(self.tr("Detect paths"))
        self.desktop_repair.setText(self.tr("Preview project cache repair"))
        self.desktop_history.setText(self.tr("Preview recovery from a historical snapshot"))
        self.thread_button.setText(self.tr("Check a specific thread or Goal"))
        self.support_button.setText(self.tr("Review support report"))
        thread_text = self.thread_report_text() if self.report else ""
        self.thread_status.setText(thread_text)
        self.thread_status.setVisible(bool(thread_text))
        for index in range(self.backups.count()):
            item = self.backups.itemData(index)
            if isinstance(item, (tuple, list)):
                self.backups.setItemText(index, self.tr("Desktop cache · %1").replace("%1", item[1]))
        self.browse_button.setText(self.tr("Choose folder"))
        self.refresh.setText(self.tr("Refresh backups"))
        self.backups.setAccessibleName(self.tr("Recovery backups"))

        location = ""
        if self.locations:
            executable = self.locations.executable
            location = self.tr("Codex CLI location: %1").replace("%1", str(executable)) if executable else self.tr("No Codex CLI was found in the local installation paths checked. You can still check a local data folder.")
        self.location_note.setText(location)
        self.location_note.setVisible(bool(location))

        result = self.message_text()
        if self.report and not self.message:
            result = "\n".join(self.finding_text(item) for item in self.report.findings)
            result = result or self.tr("No issues found in the checks that ran.")
        self.results.setText(result)
        self.results.setVisible(bool(result))

        status = ""
        if self.backup_error:
            status = self.tr("The selected backup is missing, unsafe, or failed verification.")
        elif self.backup_count:
            status = self.tr("Verified recovery backups: %1").replace("%1", str(self.backup_count))
        elif self.backup_count == 0:
            status = self.tr("No verified recovery backups found.")
        if self.show_backup_outcome and self.message:
            status = "\n".join(part for part in (self.message_text(), status) if part)
        self.backup_status.setText(status)
        self.backup_status.setVisible(bool(status))

    def thread_report_text(self) -> str:
        if self.invalid_thread:
            return self.tr("Enter a UUID in the form 00000000-0000-0000-0000-000000000000.")
        report = self.thread_report
        if report is None:
            return ""
        descriptions = {
            Presence.FOUND: self.tr("Found"),
            Presence.ABSENT: self.tr("No matching record"),
            Presence.STORE_MISSING: self.tr("Database file missing"),
            Presence.TABLE_MISSING: self.tr("Table missing"),
            Presence.SIDECARS: self.tr("Skipped because SQLite journal files are present"),
            Presence.UNAVAILABLE: self.tr("Unavailable or unsupported"),
        }
        references = {
            RolloutReference.NOT_CHECKED: self.tr("Not checked"),
            RolloutReference.NOT_RECORDED: self.tr("No rollout path recorded"),
            RolloutReference.FOUND: self.tr("Matching file exists"),
            RolloutReference.FOUND_ARCHIVED: self.tr("Matching file exists in the archive"),
            RolloutReference.MISSING: self.tr("Referenced file is missing"),
            RolloutReference.WRONG_THREAD: self.tr("Filename has a different thread UUID"),
            RolloutReference.UNAVAILABLE: descriptions[Presence.UNAVAILABLE],
        }
        links = {
            ProjectLink.NOT_CHECKED: references[RolloutReference.NOT_CHECKED],
            ProjectLink.UNAVAILABLE: descriptions[Presence.UNAVAILABLE],
            ProjectLink.UNASSIGNED: self.tr("No project link recorded"),
            ProjectLink.PROJECTLESS: self.tr("Explicitly marked projectless"),
            ProjectLink.LINKED: self.tr("Project link recorded"),
            ProjectLink.MISSING_PROJECT: self.tr("Referenced project is missing"),
            ProjectLink.INCOMPLETE_MAPPING: self.tr("Desktop project mapping is incomplete"),
            ProjectLink.CONFLICT: self.tr("Project link conflicts with the projectless marker"),
        }
        comparison = {
            ProjectComparison.NOT_COMPARABLE: "",
            ProjectComparison.AGREE: self.tr("Both stores reference the same project ID."),
            ProjectComparison.CONFLICT: self.tr("The two stores disagree about this thread's project."),
        }
        goal_status = {
            GoalStatus.ACTIVE: self.tr("Active Goal"),
            GoalStatus.PAUSED: self.tr("Paused Goal"),
            GoalStatus.BLOCKED: self.tr("Blocked Goal"),
            GoalStatus.USAGE_LIMITED: self.tr("Usage-limited Goal"),
            GoalStatus.BUDGET_LIMITED: self.tr("Budget-limited Goal"),
            GoalStatus.COMPLETE: self.tr("Completed Goal"),
            GoalStatus.UNKNOWN: self.tr("Unknown Goal status"),
        }
        goal_comparison = {
            GoalComparison.NOT_COMPARED: "",
            GoalComparison.SAME_GOAL_AND_STATUS: self.tr("Both stores have the same Goal ID and status."),
            GoalComparison.SAME_GOAL_UNKNOWN_STATUS: self.tr("The Goal ID matches, but a status is unknown."),
            GoalComparison.DIFFERENT_STATUS: self.tr("The same Goal has different recorded statuses."),
            GoalComparison.DIFFERENT_GOAL: self.tr("The stores record different Goal IDs for this thread."),
            GoalComparison.UNAVAILABLE: self.tr("Both stores have a Goal record, but their IDs could not be compared."),
        }
        history_index = {
            Presence.FOUND: self.tr("Paginated history index file: Present. Message coverage was not checked."),
            Presence.STORE_MISSING: self.tr("Paginated history index file: Missing. This does not establish message loss."),
            Presence.UNAVAILABLE: self.tr("Paginated history index file: Could not verify its presence safely."),
        }
        details = report.goal_details
        archive_status = {
            True: self.tr("Marked archived"),
            False: self.tr("Marked active"),
            None: descriptions[Presence.UNAVAILABLE],
        }
        can_review_archive = (report.archived is True and report.rollout_reference is RolloutReference.FOUND_ARCHIVED
                              and report.active_files == 0 and report.archived_files == 1)
        return "\n".join(line for line in (
            self.tr("Thread in state database: %1").replace("%1", descriptions[report.thread]),
            self.tr("Project link in state database: %1").replace("%1", links[report.project_links.database]),
            self.tr("Project link in Desktop cache: %1").replace("%1", links[report.project_links.cache]),
            comparison[report.project_links.comparison],
            self.tr("Rollout referenced by the state DB: %1").replace("%1", references[report.rollout_reference]),
            self.tr("Goal in legacy state database: %1").replace("%1",
                goal_status.get(details.legacy, descriptions[report.legacy_goal])),
            self.tr("Goal in separate database: %1").replace("%1",
                goal_status.get(details.separate, descriptions[report.separate_goal])),
            goal_comparison[details.comparison],
            self.tr("Active session rollouts: %1").replace("%1", str(report.active_files)
                if report.active_files is not None else descriptions[Presence.UNAVAILABLE]),
            self.tr("Archived session rollouts: %1").replace("%1", str(report.archived_files)
                if report.archived_files is not None else descriptions[Presence.UNAVAILABLE]),
            self.tr("Archive flag in state database: %1").replace("%1", archive_status[report.archived])
                if report.thread is Presence.FOUND else "",
            self.tr("If the chat appears in Codex under Settings > Archived chats, use Unarchive there.")
                if can_review_archive else "",
            history_index.get(report.history_index, "") if report.history_index is not None else "",
            self.tr("This check does not establish deletion or visibility in Codex Desktop."),
        ) if line)

    def message_text(self) -> str:
        return {
            "waiting": self.tr("Checking local metadata…"),
            "desktop_repaired": self.tr("Desktop cache written and verified. Restart Codex and confirm the project list and an old chat in the app."),
            "desktop_restored": self.tr("Original Desktop cache restored and verified."),
            "desktop_version": self.tr("This Desktop version is not supported by the selected recovery rule."),
            "desktop_snapshot_name": self.tr("Choose a pre-incident .codex-global-state.json, .bak, or .tmp-* snapshot."),
            "desktop_ineligible": self.tr("The cache, schema or project evidence does not qualify for this rule. Keep your files and consult the user guide."),
            "desktop_busy": self.tr("Codex or ChatGPT may be running, or another repair holds the profile lock. Fully exit the apps and try again."),
            "thread_location_changed": self.tr("The Codex database location may have changed. Run diagnostics again before checking a thread."),
            "scan_error": self.tr("The scan could not finish. Check the folder and try again."),
            "repaired": self.tr("Configuration repaired and verified. Run diagnostics again to refresh the results."),
            "restored": self.tr("Original bytes restored and verified. Run diagnostics again to refresh the results."),
            "stale": self.tr("The file or backup no longer matches the preview. Run diagnostics or refresh the backup list."),
            "backup_invalid": self.tr("The selected backup is missing, unsafe, or failed verification."),
            "rolled_back": self.tr("The change failed verification. The previous bytes were restored and verified."),
            "rollback_failed": self.tr("The change and automatic rollback could not be verified. Keep Codex closed and preserve the backup and any recovery files."),
            "io_error": self.tr("The operation was stopped. Check file permissions, close Codex, and try again."),
        }.get(self.message, "")

    def finding_text(self, item: Finding) -> str:
        text = {
            Code.HOME_MISSING: self.tr("The Codex data folder was not found."),
            Code.HOME_UNAVAILABLE: self.tr("The data folder is inaccessible or is not an ordinary folder on a local drive."),
            Code.CONFIG_MISSING: self.tr("User config.toml is absent. Codex can use defaults for this layer."),
            Code.CONFIG_UNAVAILABLE: self.tr("User configuration could not be checked safely."),
            Code.CONFIG_INVALID: self.tr("User configuration has invalid TOML, text encoding, or a SQLite path setting. Review it manually."),
            Code.CONFIG_ALL_NUL: self.tr("User configuration contains only NUL bytes. A verified reset is available."),
            Code.INDEX_UNAVAILABLE: self.tr("The session name index could not be checked safely."),
            Code.INDEX_MALFORMED: self.tr("Malformed JSON lines in the session name index: %1"),
            Code.INDEX_UNKNOWN: self.tr("Unsupported records in the session name index: %1"),
            Code.DB_MISSING: self.tr("The supported SQLite state file was not found in the selected location."),
            Code.DB_OTHER_VERSION: self.tr("A different SQLite state file version was found. Its schema was not inspected."),
            Code.DB_UNAVAILABLE: self.tr("SQLite state could not be checked safely."),
            Code.DB_SIDECARS: self.tr("SQLite journal or WAL files are present. The state check was skipped."),
            Code.DB_WAL_DIAGNOSTIC: self.tr("State metadata was read with the SQLite WAL. Cache recovery with a WAL is limited to the supported historical migration case."),
            Code.DB_DAMAGED: self.tr("SQLite reported a damaged or invalid state database. Automatic repair is unavailable."),
            Code.DB_SCHEMA: self.tr("The SQLite thread schema is unsupported."),
            Code.ROW_LIMIT: self.tr("Only the first %1 SQLite thread references were checked."),
            Code.PROJECT_MISSING: self.tr("Thread references to missing project folders: %1"),
            Code.PROJECT_UNAVAILABLE: self.tr("Thread project paths that could not be checked: %1"),
            Code.ROLLOUT_MISSING: self.tr("Missing session files referenced by SQLite: %1"),
            Code.ROLLOUT_UNAVAILABLE: self.tr("Session references outside the supported path or format: %1"),
            Code.DESKTOP_UNAVAILABLE: self.tr("Desktop metadata could not be checked safely. Close Codex and check again."),
            Code.DESKTOP_SCHEMA: self.tr("The Desktop project schema is unsupported. No project changes are offered."),
            Code.DESKTOP_PROJECTS: self.tr("Projects still present in SQLite: %1"),
            Code.DESKTOP_ROOT_MISSING: self.tr("Missing or unsafe project roots: %1. Check the original folders before attempting repair."),
            Code.DESKTOP_CACHE_EMPTY: self.tr("The Desktop project cache is empty although SQLite still has projects."),
            Code.DESKTOP_CACHE_UNAVAILABLE: self.tr("The Desktop cache is missing, invalid or unreadable. Preserve any prior snapshot and consult the user guide."),
            Code.DESKTOP_CACHE_INCOMPLETE: self.tr("SQLite projects absent from the Desktop cache: %1. A historical snapshot may help."),
            Code.DESKTOP_PROJECT_TIMESTAMPS_MISSING: self.tr("Project cache entries missing timestamp fields: %1. A version-specific repair may apply."),
            Code.DESKTOP_ROOT_SHARED: self.tr("Exact project roots shared by multiple SQLite project records: %1. Review the original registrations before re-adding folders."),
            Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING: self.tr("The Desktop cache marks project migration complete and chat assignment migration incomplete. Preserve the cache for review."),
            Code.DESKTOP_ASSIGNMENT_MIGRATION_ORPHANS: self.tr("Pending chat assignments whose threads are absent from the checked SQLite database: %1."),
            Code.DESKTOP_ASSIGNMENT_PROJECTLESS: self.tr("Pending chat IDs also explicitly marked projectless: %1. These markers do not prove the original project membership. Preserve them for review."),
            Code.DESKTOP_UNASSIGNED: self.tr("Threads without a SQLite project link: %1. This can be normal for projectless chats."),
            Code.DESKTOP_CANDIDATES: self.tr("Unassigned threads with one exact registered root match: %1. A path match alone cannot authorize relinking."),
            Code.DESKTOP_DANGLING: self.tr("Thread links to absent SQLite projects: %1. Preserve the database for manual review."),
            Code.DESKTOP_THREAD_LIMIT: self.tr("The thread count exceeds the Desktop diagnostic limit."),
            Code.GOAL_UNAVAILABLE: self.tr("A Goal store could not be checked safely."),
            Code.GOAL_SIDECARS: self.tr("The separate Goal database still has SQLite journal files. Close Codex and scan again. Keep the journal files."),
            Code.GOAL_LEGACY: self.tr("Thread Goal records in the legacy state database: %1"),
            Code.GOAL_SPLIT: self.tr("Thread Goal records in the separate Goal database: %1"),
            Code.GOAL_ORPHAN: self.tr("Goal records with no thread in the checked state database: %1. Goal repair is unavailable."),
            Code.DESKTOP_SESSION_UNAVAILABLE: self.tr("The session filename scan could not finish safely."),
            Code.DESKTOP_SESSION_UNLISTED: self.tr("Session file IDs absent from the checked thread database: %1. Try Codex Resume or review a known backup."),
            Code.DESKTOP_SESSION_DUPLICATE: self.tr("Rollout IDs present in multiple files: %1. Keep both copies for manual review."),
            Code.DESKTOP_SESSION_INDEX_ONLY: self.tr("Session name index IDs without a matching checked thread or file: %1."),
            Code.GOAL_NONE: self.tr("No Goal record was found in the checked stores. This does not establish deletion."),
            Code.GOAL_DUAL: self.tr("Both Goal stores contain records. The counts can overlap; reopen the original thread to check its Goal."),
            Code.GOAL_THREAD_CHECK_SKIPPED: self.tr("Goal records exist, but their thread links could not be checked."),
            Code.DESKTOP_SESSION_THREAD_CHECK_SKIPPED: self.tr("Session IDs found on disk without a usable thread database for comparison: %1."),
        }[item.code]
        return text.replace("%1", str(item.count))
