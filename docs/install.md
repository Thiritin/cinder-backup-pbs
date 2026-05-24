# Install

This driver replaces `cinder.backup.drivers.s3` (or whatever you have
configured today) with a PBS-backed alternative. There is no parallel
operation: only one `backup_driver` is active per cinder deployment.
Existing backups created by the prior driver will not be deletable via
cinder once you switch; either drain them first or migrate ownership.

## PBS prerequisites

1. PBS server reachable from the cluster's management network on port 8007.
2. A datastore (e.g. `RAID5`).
3. A namespace prefix (e.g. `openstack`) created on the datastore.
4. An API token with:
   - `DatastoreBackup` on `/datastore/<store>/<prefix>`
   - `DatastoreReader` on `/datastore/<store>/<prefix>`
   - `DatastoreAudit` on `/datastore/<store>/<prefix>`
   - Implicit ability to create nested namespaces via `DatastoreModify`
     (granted by `DatastorePowerUser`), OR pre-create per-project
     namespaces from your provisioning tooling.
5. PBS server's TLS cert SHA-256 fingerprint.

Example:

```bash
proxmox-backup-manager user generate-token cinder@pbs nightly
proxmox-backup-manager acl update /datastore/RAID5/openstack \
    DatastoreBackup --auth-id 'cinder@pbs!nightly'
proxmox-backup-manager acl update /datastore/RAID5/openstack \
    DatastoreReader --auth-id 'cinder@pbs!nightly'
```

## Build the image

```bash
REGISTRY=registry.eu-west-1.cloud.pawhost.de ./ci/build-image.sh
```

The build:

* Starts from upstream `quay.io/airshipit/cinder:2026.1-ubuntu_noble`.
* Adds the Proxmox apt repo and installs `proxmox-backup-client`,
  `ceph-common`, `rbd-nbd`.
* Pip-installs this package, registering the `cinder.backup.drivers`
  entry point for `pbs`.

## Configure cinder

`values/cinder/values.yaml` delta:

```yaml
images:
  tags:
    cinder_backup: "registry.eu-west-1.cloud.pawhost.de/cinder-backup-pbs:<tag>"

conf:
  cinder:
    DEFAULT:
      backup_driver: cinder_backup_pbs.driver.PbsBackupDriver
    pbs_backup:
      repository: "cinder@pbs!nightly@10.100.72.80:RAID5"
      fingerprint: "e6:8e:9d:...:2d"
      password_file: /etc/cinder/pbs-token
      namespace_prefix: openstack
      rbd_pool: cinder-volumes
      rbd_user: cinder
      rbd_keyring: /etc/ceph/ceph.client.cinder.keyring
      pbs_tmpdir: /pbs-tmp
      pbs_cache_dir: /pbs-tmp/cache

# Token secret rendered into a Kubernetes Secret + file-mounted at
# /etc/cinder/pbs-token (read-only, mode 0400).
```

## Pod security + volume mounts

Cinder-backup pods must run privileged and mount:

* `/dev` (hostPath) — for `/dev/nbd*` block devices
* `/sys` (hostPath) — required by `rbd-nbd`
* `/lib/modules` (hostPath, read-only) — `rbd-nbd` may need to ensure
  the `nbd` module is loaded
* `/pbs-tmp` (emptyDir, medium=Memory) — tmpfs for `pbc` cache; without
  this, `pbc` fails restore with `Operation not supported (os error 95)`
  because container `/tmp` is overlayfs.

Helm fragment (openstack-helm-style):

```yaml
pod:
  security_context:
    cinder_backup:
      pod:
        privileged: true
  mounts:
    cinder_backup:
      cinder_backup:
        volumeMounts:
          - name: host-dev
            mountPath: /dev
          - name: host-sys
            mountPath: /sys
          - name: host-modules
            mountPath: /lib/modules
            readOnly: true
          - name: pbs-tmp
            mountPath: /pbs-tmp
        volumes:
          - name: host-dev
            hostPath: { path: /dev }
          - name: host-sys
            hostPath: { path: /sys }
          - name: host-modules
            hostPath: { path: /lib/modules }
          - name: pbs-tmp
            emptyDir: { medium: Memory, sizeLimit: 1Gi }
```

## Verify

```bash
# Create a small volume, back it up, restore it.
openstack volume create --size 1 pbs-smoke
openstack volume backup create --name smoke pbs-smoke
openstack volume backup list
openstack volume backup restore <backup-id> <new-vol-id>
openstack volume backup delete <backup-id>
```

Check PBS:

```bash
proxmox-backup-client snapshot list --ns openstack/<project_id> \
  --repository cinder@pbs!nightly@10.100.72.80:RAID5
```
