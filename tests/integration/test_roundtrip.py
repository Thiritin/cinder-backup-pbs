"""Live backup -> restore -> compare round trip against real PBS + Ceph.

Skipped unless the PBS_IT_* env vars are set (see conftest + README).
Creates two throwaway RBD images, backs the source up to PBS, restores
into the target, and asserts the bytes match. Cleans up snapshots,
images, and the PBS backup it created.
"""
import subprocess
import uuid

import pytest

from cinder_backup_pbs.pbs_client import PbsClient
from cinder_backup_pbs.rbd_helper import RbdHelper

IMG_SIZE_MB = 16


def _rbd(env, *args):
    cmd = ["rbd", "--id", env["rbd_user"], "-p", env["rbd_pool"]]
    if env.get("rbd_keyring"):
        cmd += ["--keyring", env["rbd_keyring"]]
    cmd += list(args)
    subprocess.run(cmd, check=True)


@pytest.fixture
def images(it_env):
    suffix = uuid.uuid4().hex[:8]
    src = f"pbs-it-src-{suffix}"
    dst = f"pbs-it-dst-{suffix}"
    _rbd(it_env, "create", "--size", f"{IMG_SIZE_MB}M", src)
    _rbd(it_env, "create", "--size", f"{IMG_SIZE_MB}M", dst)
    try:
        yield src, dst
    finally:
        for img in (src, dst):
            subprocess.run(
                ["rbd", "--id", it_env["rbd_user"], "-p", it_env["rbd_pool"],
                 "rm", img],
                check=False,
            )


def _dev_sha(dev, size_mb):
    out = subprocess.run(
        ["dd", f"if={dev}", "bs=1M", f"count={size_mb}", "status=none"],
        check=True, capture_output=True,
    ).stdout
    import hashlib
    return hashlib.sha256(out).hexdigest()


def test_backup_restore_roundtrip(it_env, images):
    src, dst = images
    rbd = RbdHelper(
        pool=it_env["rbd_pool"],
        user=it_env["rbd_user"],
        keyring=it_env.get("rbd_keyring"),
    )
    pbs = PbsClient(
        repository=it_env["repository"],
        fingerprint=it_env["fingerprint"],
        password_file=it_env["password_file"],
        tmpdir="/pbs-tmp",
        cache_dir="/pbs-tmp/cache",
    )
    ns = it_env["namespace"]
    backup_id = f"vol-{uuid.uuid4()}"
    backup_time = 1779646459

    # Write a known pattern into the source image.
    with rbd.mapped(src, read_only=False) as dev:
        subprocess.run(
            ["dd", "if=/dev/urandom", f"of={dev}", "bs=1M",
             f"count={IMG_SIZE_MB}", "conv=fsync", "status=none"],
            check=True,
        )
        src_sha = _dev_sha(dev, IMG_SIZE_MB)

    pbs.ensure_namespace(ns)
    stage = f"pbs-it-{uuid.uuid4().hex[:8]}"
    snapshot_path = None
    try:
        with rbd.staged_snapshot(src, stage):
            with rbd.mapped(f"{src}@{stage}", read_only=True) as dev:
                snapshot_path = pbs.backup_image(
                    source_device=dev, namespace=ns,
                    backup_id=backup_id, backup_time=backup_time,
                )

        # Restore into the target image via pbc stdout -> dd.
        with rbd.mapped(dst, read_only=False) as dev:
            dd = subprocess.Popen(
                ["dd", f"of={dev}", "bs=4M", "conv=fsync", "status=none"],
                stdin=subprocess.PIPE,
            )
            try:
                pbs.restore_image_to_stdout(
                    snapshot_path=snapshot_path, namespace=ns,
                    target_stdout=dd.stdin,
                )
            finally:
                if dd.stdin and not dd.stdin.closed:
                    dd.stdin.close()
            assert dd.wait(timeout=300) == 0
            dst_sha = _dev_sha(dev, IMG_SIZE_MB)

        assert dst_sha == src_sha
    finally:
        if snapshot_path:
            pbs.forget(snapshot_path, ns)
