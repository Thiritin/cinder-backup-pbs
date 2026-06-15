"""rbd / rbd-nbd shell helpers.

Why rbd-nbd and not the kernel rbd module:
* kernel rbd hangs on images that have ``object-map`` + ``fast-diff`` +
  ``deep-flatten`` features enabled (cinder defaults).
* rbd-nbd is userspace, supports every feature flag, and gives clean
  exit codes on error.

All helpers are blocking subprocess calls; they are short-lived and
called per-backup, so the cost is irrelevant compared to actual data
transfer.
"""
from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator

from oslo_log import log as logging

from cinder_backup_pbs.exceptions import RbdHelperError

LOG = logging.getLogger(__name__)


class RbdHelper:
    def __init__(
        self,
        pool: str,
        user: str = "cinder",
        keyring: str | None = None,
    ) -> None:
        self.pool = pool
        self.user = user
        self.keyring = keyring

    # -- low-level ------------------------------------------------------

    def _auth(self) -> list[str]:
        """Common auth flags for rbd / rbd-nbd. Includes --keyring only when
        configured; otherwise ceph falls back to its default keyring search."""
        args = ["--id", self.user]
        if self.keyring:
            args += ["--keyring", self.keyring]
        return args

    def _run(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        LOG.debug("rbd cmd: %s", " ".join(args))
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            raise RbdHelperError(
                f"{args[0]} failed: rc={proc.returncode} stderr={err}"
            )
        return proc

    # -- snapshot lifecycle --------------------------------------------

    def snap_create(self, image: str, snap_name: str) -> None:
        self._run(
            ["rbd", *self._auth(), "-p", self.pool,
             "snap", "create", f"{image}@{snap_name}"]
        )

    def snap_protect(self, image: str, snap_name: str) -> None:
        self._run(
            ["rbd", *self._auth(), "-p", self.pool,
             "snap", "protect", f"{image}@{snap_name}"]
        )

    def snap_unprotect(self, image: str, snap_name: str) -> None:
        self._run(
            ["rbd", *self._auth(), "-p", self.pool,
             "snap", "unprotect", f"{image}@{snap_name}"]
        )

    def snap_rm(self, image: str, snap_name: str) -> None:
        self._run(
            ["rbd", *self._auth(), "-p", self.pool,
             "snap", "rm", f"{image}@{snap_name}"]
        )

    # -- nbd map/unmap --------------------------------------------------

    def nbd_map(self, target: str, read_only: bool = False) -> str:
        """Map ``<pool>/<image>[@<snap>]`` via rbd-nbd. Returns the /dev/nbdN path."""
        args = ["rbd-nbd", *self._auth(), "map", f"{self.pool}/{target}"]
        if read_only:
            args.append("--read-only")
        proc = self._run(args, timeout=60)
        dev = proc.stdout.decode("utf-8", "replace").strip()
        if not dev.startswith("/dev/"):
            raise RbdHelperError(f"rbd-nbd map: unexpected output {dev!r}")
        return dev

    def nbd_unmap(self, device: str) -> None:
        self._run(["rbd-nbd", "unmap", device], timeout=60)

    # -- context managers ----------------------------------------------

    @contextlib.contextmanager
    def staged_snapshot(self, image: str, snap_name: str) -> Iterator[None]:
        """Create + protect a snapshot for the duration of the block.

        Always tries to unprotect + remove on exit, even if the body raised.
        """
        self.snap_create(image, snap_name)
        try:
            self.snap_protect(image, snap_name)
            try:
                yield
            finally:
                try:
                    self.snap_unprotect(image, snap_name)
                except RbdHelperError as e:
                    LOG.warning("snap unprotect %s@%s: %s", image, snap_name, e)
        finally:
            try:
                self.snap_rm(image, snap_name)
            except RbdHelperError as e:
                LOG.warning("snap rm %s@%s: %s", image, snap_name, e)

    @contextlib.contextmanager
    def mapped(self, target: str, read_only: bool = False) -> Iterator[str]:
        """Yield a /dev/nbdN path for the lifetime of the block."""
        dev = self.nbd_map(target, read_only=read_only)
        try:
            yield dev
        finally:
            try:
                self.nbd_unmap(dev)
            except RbdHelperError as e:
                LOG.warning("nbd unmap %s: %s", dev, e)
