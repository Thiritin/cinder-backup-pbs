"""Thin subprocess wrapper around `proxmox-backup-client`.

The wrapper exists so the rest of the driver does not have to deal with
argv construction, environment variables, secret loading, or stderr
parsing. All methods raise `PbsBackupError` on non-zero exit.

Environment requirements (set by the wrapper, do not need to be set
elsewhere):

* ``TMPDIR`` and ``XDG_CACHE_HOME`` are pointed at a real tmpfs.
  pbc opens ``O_TMPFILE`` in both; overlayfs returns EOPNOTSUPP.
* ``PBS_REPOSITORY``, ``PBS_FINGERPRINT``, ``PBS_PASSWORD`` are passed in
  the environment so the secret never appears on argv (visible in /proc).
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from oslo_log import log as logging

from cinder_backup_pbs.exceptions import PbsBackupError

LOG = logging.getLogger(__name__)

# pbc has no stable machine-readable error codes for these cases, so we match
# on substrings. Keep the sets broad to survive wording changes across pbc
# releases. Matched case-insensitively against decoded stderr.
_ALREADY_EXISTS_MARKERS = (
    "already exists",
    "already present",
)
_NOT_FOUND_MARKERS = (
    "no such file",
    "not found",
    "does not exist",
    "unable to load",
)


def _stderr_has(error: Exception, markers: tuple[str, ...]) -> bool:
    msg = str(error).lower()
    return any(m in msg for m in markers)


class PbsClient:
    BIN = "/usr/bin/proxmox-backup-client"

    def __init__(
        self,
        repository: str,
        fingerprint: str,
        password_file: str,
        tmpdir: str,
        cache_dir: str,
    ) -> None:
        self.repository = repository
        self.fingerprint = fingerprint
        self.password_file = password_file
        self.tmpdir = tmpdir
        self.cache_dir = cache_dir

    # -- environment ----------------------------------------------------

    def _password(self) -> str:
        with open(self.password_file, encoding="utf-8") as f:
            return f.read().strip()

    def _env(self) -> dict:
        return {
            **os.environ,
            "PBS_REPOSITORY": self.repository,
            "PBS_FINGERPRINT": self.fingerprint,
            "PBS_PASSWORD": self._password(),
            "TMPDIR": self.tmpdir,
            "XDG_CACHE_HOME": self.cache_dir,
        }

    def _run(
        self,
        args: list[str],
        timeout: int | None = None,
        stdout=None,
        stdin=None,
    ) -> subprocess.CompletedProcess:
        cmd = [self.BIN, *args]
        LOG.info("pbc invoke: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                env=self._env(),
                stdout=stdout if stdout is not None else subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=stdin,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise PbsBackupError(
                f"pbc {args[0]} timed out after {timeout}s"
            ) from e

        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            raise PbsBackupError(
                f"pbc {args[0]} failed: rc={proc.returncode} stderr={err}"
            )
        return proc

    # -- namespace ------------------------------------------------------

    def ensure_namespace(self, ns: str) -> None:
        """Create a (possibly nested) namespace, ignore 'already exists'."""
        try:
            self._run(["namespace", "create", ns], timeout=30)
        except PbsBackupError as e:
            if _stderr_has(e, _ALREADY_EXISTS_MARKERS):
                return
            raise

    # -- backup ---------------------------------------------------------

    def backup_image(
        self,
        source_device: str,
        namespace: str,
        backup_id: str,
        backup_time: int,
        archive_name: str = "vm.img",
        backup_type: str = "vm",
        timeout: int | None = None,
    ) -> str:
        """Run `pbc backup <archive>.img:<dev>` and return the ISO snapshot id.

        Returns the snapshot path: e.g. ``vm/<backup-id>/2026-05-24T18:14:19Z``.
        Caller should persist it in cinder ``backup.service_metadata`` for
        later restore / delete.
        """
        spec = f"{archive_name}:{source_device}"
        self._run(
            [
                "backup",
                spec,
                "--ns",
                namespace,
                "--backup-type",
                backup_type,
                "--backup-id",
                backup_id,
                "--backup-time",
                str(backup_time),
            ],
            timeout=timeout,
        )
        iso = datetime.fromtimestamp(backup_time, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return f"{backup_type}/{backup_id}/{iso}"

    # -- restore --------------------------------------------------------

    def restore_image_to_stdout(
        self,
        snapshot_path: str,
        namespace: str,
        target_stdout,
        archive_name: str = "vm.img",
        timeout: int | None = None,
    ) -> None:
        """Stream restored bytes to a writable stdout sink (e.g. dd subprocess).

        pbc rejects writing directly to an existing block device (it uses
        ``O_CREAT|O_EXCL`` and refuses with EEXIST), so the only reliable
        way to restore onto ``/dev/nbdN`` is to pipe stdout.
        """
        self._run(
            [
                "restore",
                snapshot_path,
                archive_name,
                "-",
                "--ns",
                namespace,
            ],
            timeout=timeout,
            stdout=target_stdout,
        )

    # -- delete ---------------------------------------------------------

    def forget(self, snapshot_path: str, namespace: str) -> None:
        try:
            self._run(
                ["snapshot", "forget", snapshot_path, "--ns", namespace],
                timeout=120,
            )
        except PbsBackupError as e:
            # 'not found' is fine on retry / double-delete
            if _stderr_has(e, _NOT_FOUND_MARKERS):
                LOG.warning("forget(%s) no-op: already gone", snapshot_path)
                return
            raise
