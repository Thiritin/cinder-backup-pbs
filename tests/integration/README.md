# Integration tests

These exercise the real `proxmox-backup-client` + `rbd-nbd` round trip:
create an RBD image, back it up to a live PBS datastore, restore it, and
assert byte-for-byte equality. They are **skipped by default** and only
run when the required environment is present.

## Requirements

* A reachable PBS server + datastore, with an API token that can backup,
  read, and forget snapshots in a throwaway namespace.
* A Ceph cluster with an RBD pool and a keyring for the test user.
* `proxmox-backup-client`, `rbd`, `rbd-nbd` on PATH.
* The host `nbd` module loaded (`modprobe nbd nbds_max=64`).
* Run as a user that can map `/dev/nbd*` (usually root).

## Configure

Set these before running:

```bash
export PBS_IT_REPOSITORY='cinder@pbs!ci@pbs.example.com:RAID5'
export PBS_IT_FINGERPRINT='e6:8e:...:2d'
export PBS_IT_PASSWORD_FILE=/etc/pbs/ci-token
export PBS_IT_NAMESPACE='openstack/ci'        # throwaway; test forgets after
export PBS_IT_RBD_POOL='cinder-volumes'
export PBS_IT_RBD_USER='cinder'
export PBS_IT_RBD_KEYRING=/etc/ceph/ceph.client.cinder.keyring
```

## Run

```bash
pip install -e '.[test]'
pytest tests/integration -v
```

Without the `PBS_IT_*` vars set, every test in this directory is skipped,
so it is safe to include in a normal `pytest` invocation.

> CI does not run these — they need real PBS + Ceph. Run them by hand
> before tagging a release, or wire them into a self-hosted runner that
> has the backends.
