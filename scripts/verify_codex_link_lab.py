"""Verify Codex link and archive operations in the isolated Windows lab."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from tempfile import mkdtemp
from threading import Thread
from time import monotonic
from uuid import UUID, uuid4

from ai_repair_kit.tools.desktop_catalog import (
    STATE_NAME, check_rollout_file, columns, local_rollout_path, open_snapshot, projects, rollout_identity,
)


from ai_repair_kit.tools.desktop_thread import GoalStatus, RolloutReference, inspect_thread


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def powershell_json(command: str) -> dict:
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; " + command],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=True,
    )
    return json.loads(result.stdout)


def lab_executable(expected_uuid: UUID) -> Path:
    require(os.name == "nt" and os.environ.get("USERNAME") == "ArkLab",
            "Use the local ArkLab account in the offline Windows guest.")
    machine = powershell_json(
        "Get-CimInstance Win32_ComputerSystemProduct | Select-Object Name,UUID | ConvertTo-Json"
    )
    require("VirtualBox" in machine["Name"] and UUID(machine["UUID"]) == expected_uuid,
            "The guest identity does not match.")
    isolated = powershell_json(
        "@{external=@(Get-NetIPAddress | Where-Object {$_.IPAddress -notin @('127.0.0.1','::1')}).Count;"
        "routes=@(Get-NetRoute | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0')}).Count;"
        "shares=@(Get-SmbMapping).Count} | ConvertTo-Json"
    )
    require(not any(isolated.values()), "The guest has an external address, default route, or SMB mapping.")
    package = powershell_json(
        "$p=Get-AppxPackage -Name OpenAI.Codex; "
        "@{Version=$p.Version.ToString();Status=$p.Status.ToString();"
        "InstallLocation=$p.InstallLocation} | ConvertTo-Json"
    )
    require(isinstance(package, dict) and package["Version"] == "26.1029.7803.0"
            and package["Status"] == "Ok", "Expected the lab copy of Codex MSIX 26.1029.7803.0.")
    return Path(package["InstallLocation"]) / "app/resources/codex.exe"


class AppServer:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.lines: Queue[str | None] = Queue()
        self.request_id = 0
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def _send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.request_id += 1
        self._send({"id": self.request_id, "method": method, "params": params})
        deadline = monotonic() + 20
        for _ in range(80):
            try:
                line = self.lines.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                raise RuntimeError(f"{method} timed out.") from None
            require(line is not None, f"{method} lost its connection.")
            reply = json.loads(line)
            if reply.get("id") == self.request_id:
                return reply
        raise RuntimeError(f"{method} returned too many unrelated messages.")

    def call(self, method: str, **params):
        reply = self.request(method, params)
        if "error" in reply:
            raise RuntimeError(f"{method} failed with RPC code {reply['error']['code']}.")
        return reply["result"]

    def initialize(self) -> None:
        result = self.call(
            "initialize",
            clientInfo={"name": "ark_link_lab", "title": "AI Repair Kit link lab", "version": "1.0.0"},
            capabilities={"experimentalApi": True},
        )
        require("/0.166.0-alpha.6 " in result["userAgent"], "Unexpected App Server build.")
        self._send({"method": "initialized"})


@contextmanager
def open_server(executable: Path, profile: Path):
    environment = dict(os.environ, CODEX_HOME=str(profile), CODEX_SQLITE_HOME=str(profile))
    with subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        cwd=profile, env=environment, text=True, encoding="utf-8",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        server = AppServer(process)
        try:
            server.initialize()
            yield server
        finally:
            assert process.stdin is not None
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("The synthetic App Server did not exit cleanly.") from None
            server.reader.join(timeout=2)
            require(process.returncode == 0, "The synthetic App Server returned a failure.")


def fixture_thread(server: AppServer, profile: Path, name: str) -> tuple[str, str]:
    root = profile / name
    root.mkdir()
    project = server.call("project/create", name=name, roots=[{"path": str(root)}],
                          idempotencyKey=name)["project"]["id"]
    thread = server.call("thread/start", cwd=str(root), projectId=project)["thread"]
    require(thread.get("projectId") == project, "Could not assign the fixture thread.")
    server.call("thread/inject_items", threadId=thread["id"], items=[{
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "Synthetic lab item."}],
    }])
    return thread["id"], project


def read_link(server: AppServer, thread: str) -> str | None:
    return server.call("thread/read", threadId=thread, includeTurns=False)["thread"].get("projectId")


def set_link(server: AppServer, thread: str, project: str) -> str | None:
    return server.call("thread/metadata/update", threadId=thread, projectId=project)["thread"].get("projectId")


@dataclass(frozen=True)
class LinkPlan:
    thread: str
    project: str
    cwd: str
    rollout: str
    roots: tuple[str, ...]


def read_link_evidence(db: sqlite3.Connection, thread: str, project: str) -> tuple[str | None, LinkPlan]:
    schema = columns(db, "threads")
    require({"id", "project_id", "cwd", "rollout_path"} <= schema.keys()
            and {key for key, column in schema.items() if column[5]} == {"id"},
            "Unsupported thread identity schema.")
    row = db.execute("SELECT project_id, cwd, rollout_path FROM main.threads WHERE id COLLATE BINARY = ?",
                     (thread,)).fetchone()
    catalog = projects(db)
    require(row is not None and project in catalog, "The selected thread or project is absent.")
    current, cwd, rollout = row
    roots = catalog[project].roots
    require(isinstance(cwd, str) and isinstance(rollout, str) and roots, "Incomplete fixture metadata.")
    return current, LinkPlan(thread, project, cwd, rollout, roots)


def apply_fixture_link(profile: Path, plan: LinkPlan, *, reverse: bool = False) -> bool:
    expected, desired = (plan.project, None) if reverse else (None, plan.project)
    database = profile / STATE_NAME
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            current, facts = read_link_evidence(db, plan.thread, plan.project)
            if current != expected or facts != plan:
                return False
            path = local_rollout_path(profile, plan.rollout)
            identity = rollout_identity(path.name)
            require(identity is not None and identity[0] == plan.thread, "Unexpected fixture rollout identity.")
            check_rollout_file(path)
            require(db.execute("PRAGMA quick_check(1)").fetchone() == ("ok",), "Fixture integrity check failed.")
            require(db.execute(
                "SELECT 1 FROM main.sqlite_schema WHERE type='trigger' AND tbl_name='threads'"
            ).fetchone() is None, "Unexpected trigger on the fixture thread table.")
            result = db.execute(
                "UPDATE main.threads SET project_id = ? WHERE id COLLATE BINARY = ? AND project_id IS ?",
                (desired, plan.thread, expected),
            )
            require(result.rowcount == 1 and db.total_changes == 1, "Unexpected fixture write scope.")
            require(read_link_evidence(db, plan.thread, plan.project) == (desired, plan)
                    and db.execute("PRAGMA quick_check(1)").fetchone() == ("ok",),
                    "Fixture post-write verification failed.")
            db.commit()
            return True
        finally:
            if db.in_transaction:
                db.rollback()


def manifest(root: Path) -> dict[str, str]:
    result = {}
    for directory, folders, files in os.walk(root):
        for name in folders + files:
            path = Path(directory) / name
            info = path.lstat()
            require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "A synthetic fixture contains an unexpected filesystem link.")
            if name in files:
                require(stat.S_ISREG(info.st_mode), "Expected an ordinary synthetic file.")
                with path.open("rb") as stream:
                    result[str(path.relative_to(root))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def state_hashes(profile: Path) -> dict[str, str]:
    result = {}
    for path in profile.glob(STATE_NAME + "*"):
        with path.open("rb") as stream:
            result[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def run_fixture(executable: Path, lab: Path, *, experimental_sqlite: bool = False) -> None:
    profile = lab / "fixture"
    backup = lab / "verified-original"
    displaced = lab / "after-mutation"
    sessions = profile / "sessions"
    profile.mkdir()

    def check(server: AppServer, expected: str | None) -> None:
        require(read_link(server, target) == expected and read_link(server, sibling) == sibling_project,
                "Unexpected target or sibling assignment.")
        goal = server.call("thread/goal/get", threadId=target)["goal"]
        require(goal == expected_goal, "The target Goal changed.")

    with open_server(executable, profile) as server:
        target, target_project = fixture_thread(server, profile, "synthetic-target")
        sibling, sibling_project = fixture_thread(server, profile, "synthetic-sibling")
        expected_goal = server.call("thread/goal/set", threadId=target,
                                    objective="Synthetic lab Goal.", status="paused")["goal"]
        check(server, target_project)
        require(set_link(server, target, "") is None, "Could not reproduce a missing assignment.")
        check(server, None)

    before = manifest(profile)
    rollout_bytes = manifest(sessions)
    require(sum(name.endswith(".jsonl") for name in rollout_bytes) == 2, "Expected two persisted rollouts.")
    require("state_5.sqlite" in before and "goals_1.sqlite" in before, "Missing fixture databases.")
    shutil.copytree(profile, backup)
    require(manifest(backup) == before, "The fixture backup did not verify.")
    print("PASS verified backup of the stopped synthetic profile.", flush=True)

    with open_server(executable, profile) as server:
        rejected = server.request("thread/metadata/update", {
            "threadId": target, "projectId": str(uuid4()),
        })
        require("error" in rejected, "An unknown project was accepted.")
        check(server, None)
        require(set_link(server, target, target_project) == target_project, "Assignment failed.")

    with open_server(executable, profile) as server:
        check(server, target_project)
        require(set_link(server, target, target_project) == target_project, "Assignment retry failed.")
        check(server, target_project)
        require(set_link(server, target, "") is None, "API rollback failed.")

    with open_server(executable, profile) as server:
        check(server, None)
        require(manifest(sessions) == rollout_bytes, "The synthetic rollouts changed.")
        print("PASS invalid project refused; assignment, retry and API rollback verified.", flush=True)
        require(set_link(server, target, target_project) == target_project, "Backup restore setup failed.")

    # Keep the post-mutation copy. Restore at the original path to retain the
    # absolute rollout references stored in the synthetic state database.
    require(profile.resolve() == profile and backup.resolve() == backup
            and not displaced.exists() and manifest(backup) == before,
            "The fixture or verified backup failed restore preflight.")
    profile.rename(displaced)
    shutil.copytree(backup, profile)
    require(manifest(profile) == before, "The restored fixture differs from its backup.")
    with open_server(executable, profile) as server:
        check(server, None)
    require(manifest(sessions) == rollout_bytes and manifest(backup) == before,
            "The synthetic rollouts or the verified backup changed.")
    print("PASS file backup restored; sibling link, Goal and rollout bytes preserved.", flush=True)

    if not experimental_sqlite:
        return

    database = profile / STATE_NAME
    state_before = state_hashes(profile)
    with open_snapshot(database, allow_wal=True) as snapshot:
        current, plan = read_link_evidence(snapshot.connection, target, target_project)
        require(current is None, "Expected an unassigned target in the repair preview.")
    require(state_hashes(profile) == state_before, "The read-only preview changed a state file.")

    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            apply_fixture_link(profile, plan)
        except sqlite3.OperationalError as error:
            require(error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY, "Unexpected writer refusal.")
        else:
            raise RuntimeError("The transaction did not refuse the competing writer.")
        finally:
            writer.rollback()

    with open_server(executable, profile) as server:
        require(set_link(server, target, sibling_project) == sibling_project, "Could not set the competing link.")
    stale_bytes = state_hashes(profile)
    require(not apply_fixture_link(profile, plan), "A stale repair preview overwrote a later assignment.")
    if stale_bytes != state_hashes(profile):
        print("OBSERVED SQLite changed state file bytes when opening the rejected write transaction.", flush=True)
    with open_server(executable, profile) as server:
        check(server, sibling_project)
        require(set_link(server, target, "") is None, "Could not reset the synthetic failure state.")

    require(apply_fixture_link(profile, plan), "The conditional transaction did not restore the project link.")
    with open_server(executable, profile) as server:
        check(server, target_project)
        require(set_link(server, target, sibling_project) == sibling_project, "Could not set the later assignment.")
    require(not apply_fixture_link(profile, plan, reverse=True), "A stale rollback overwrote a later assignment.")
    with open_server(executable, profile) as server:
        check(server, sibling_project)
        require(set_link(server, target, target_project) == target_project, "Could not reset the synthetic link.")
    require(apply_fixture_link(profile, plan, reverse=True), "The conditional rollback failed.")
    with open_server(executable, profile) as server:
        check(server, None)

    require(manifest(sessions) == rollout_bytes and manifest(backup) == before,
            "The conditional transaction changed the sessions or the verified backup.")
    print("PASS conditional repair and rollback refused stale links and a competing SQLite writer.", flush=True)
    print("PASS Codex reopened the expected links; sibling, Goal, rollout bytes and backup preserved.", flush=True)


def run_archive_fixture(executable: Path, lab: Path) -> None:
    profile = lab / "fixture"
    backup = lab / "verified-archive"
    profile.mkdir()

    def list_ids(server: AppServer, *, archived: bool) -> set[str]:
        page = server.call("thread/list", archived=archived, sourceKinds=["appServer", "vscode", "cli"],
                           modelProviders=[], useStateDbOnly=True, limit=20)
        require(page.get("nextCursor") is None, "Unexpected pagination in the synthetic profile.")
        return {thread["id"] for thread in page["data"]}

    def check_app_server(server: AppServer, *, archived: bool) -> None:
        active = list_ids(server, archived=False)
        archive = list_ids(server, archived=True)
        require(sibling in active and sibling not in archive, "The sibling listing changed.")
        require((target in archive, target in active) == (archived, not archived),
                "Unexpected target thread listing.")
        require(read_link(server, target) == target_project and read_link(server, sibling) == sibling_project,
                "The target or sibling project assignment changed.")
        require(server.call("thread/goal/get", threadId=target)["goal"] == goal, "The target Goal changed.")

    def check_files(*, archived: bool) -> None:
        before = manifest(profile)
        report = inspect_thread(profile, profile, target)
        expected_reference = RolloutReference.FOUND_ARCHIVED if archived else RolloutReference.FOUND
        print(f"Archive diagnostic: flag={report.archived}, reference={report.rollout_reference.name}, "
              f"active={report.active_files}, archived={report.archived_files}.", flush=True)
        require(report.archived is archived and report.rollout_reference is expected_reference,
                "The archive flag and rollout reference did not agree.")
        require((report.active_files, report.archived_files) == ((0, 1) if archived else (1, 0)),
                "The target rollout was missing or duplicated.")
        require(report.goal_details.separate is GoalStatus.PAUSED, "The diagnostic did not find the paused Goal.")
        require(manifest(profile) == before, "AI Repair Kit diagnosis changed the fixture.")
        active = manifest(profile / "sessions")
        stored = manifest(profile / "archived_sessions")
        if archived:
            require(active == {sibling_path: original[sibling_path]}, "The active rollout bytes changed.")
            require(stored == {Path(target_path).name: original[target_path]}, "The archived rollout bytes changed.")
        else:
            require(active == original and not stored, "Unarchive did not preserve the original rollout layout and bytes.")

    with open_server(executable, profile) as server:
        target, target_project = fixture_thread(server, profile, "archive-target")
        sibling, sibling_project = fixture_thread(server, profile, "archive-sibling")
        goal = server.call("thread/goal/set", threadId=target, objective="Synthetic archive Goal.",
                           status="paused")["goal"]

    original = manifest(profile / "sessions")
    target_path, = (path for path in original if (identity := rollout_identity(Path(path).name)) and identity[0] == target)
    sibling_path, = (path for path in original if (identity := rollout_identity(Path(path).name)) and identity[0] == sibling)
    check_files(archived=False)

    with open_server(executable, profile) as server:
        check_app_server(server, archived=False)
        server.call("thread/archive", threadId=target)
    check_files(archived=True)
    archived_manifest = manifest(profile)
    shutil.copytree(profile, backup)
    require(manifest(backup) == archived_manifest, "The stopped archived fixture backup did not verify.")
    print("PASS archived chat diagnosis and verified backup.", flush=True)

    with open_server(executable, profile) as server:
        check_app_server(server, archived=True)
        require(server.call("thread/unarchive", threadId=target)["thread"]["id"] == target,
                "Unarchive returned a different thread.")
    check_files(archived=False)

    with open_server(executable, profile) as server:
        check_app_server(server, archived=False)
        server.call("thread/archive", threadId=target)
    check_files(archived=True)

    with open_server(executable, profile) as server:
        check_app_server(server, archived=True)
    require(manifest(backup) == archived_manifest, "The verified backup changed.")
    print("PASS native unarchive and re-archive preserved the chat ID, both rollout hashes, projects and Goal.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest-uuid", type=UUID, required=True)
    parser.add_argument("--run-synthetic-test", action="store_true", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--experimental-sqlite", action="store_true", help="probe direct writes in the fixture")
    mode.add_argument("--verify-archive", action="store_true", help="verify native archive, unarchive and re-archive")
    args = parser.parse_args()
    try:
        executable = lab_executable(args.guest_uuid)
        lab = Path(mkdtemp(prefix="ark-link-lab-", dir=Path.home())).resolve(strict=True)
        if args.verify_archive:
            run_archive_fixture(executable, lab)
        else:
            run_fixture(executable, lab, experimental_sqlite=args.experimental_sqlite)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # The controlled assertions are safe to display. Do not print raw process or path errors.
        parser.exit(1, f"FAIL {error if type(error) is RuntimeError else type(error).__name__}\n")


if __name__ == "__main__":
    main()
