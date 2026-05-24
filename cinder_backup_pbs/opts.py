from oslo_config import cfg

pbs_opts = [
    cfg.StrOpt(
        "repository",
        help=(
            "PBS repository URL: <token-id>@<host>:<datastore>. "
            "Example: cinder@pbs!nightly@10.100.72.80:RAID5"
        ),
    ),
    cfg.StrOpt(
        "fingerprint",
        help="SHA-256 fingerprint of the PBS server TLS certificate.",
    ),
    cfg.StrOpt(
        "password_file",
        default="/etc/cinder/pbs-token",
        help="Path to a file containing the PBS API token secret.",
    ),
    cfg.StrOpt(
        "namespace_prefix",
        default="openstack",
        help=(
            "PBS namespace under which per-project namespaces are created. "
            "Final namespace is '<prefix>/<project_id>'."
        ),
    ),
    cfg.StrOpt(
        "rbd_pool",
        default="cinder-volumes",
        help="Ceph pool that holds cinder volumes.",
    ),
    cfg.StrOpt(
        "rbd_user",
        default="cinder",
        help="Ceph client name (without 'client.' prefix).",
    ),
    cfg.StrOpt(
        "rbd_keyring",
        default="/etc/ceph/ceph.client.cinder.keyring",
        help="Path to the Ceph keyring file for rbd_user.",
    ),
    cfg.IntOpt(
        "backup_timeout",
        default=6 * 3600,
        help="Hard timeout for a single backup invocation, seconds.",
    ),
    cfg.IntOpt(
        "restore_timeout",
        default=6 * 3600,
        help="Hard timeout for a single restore invocation, seconds.",
    ),
    cfg.StrOpt(
        "pbs_tmpdir",
        default="/pbs-tmp",
        help=(
            "Real tmpfs directory used as TMPDIR for proxmox-backup-client. "
            "Must NOT be on overlayfs; pbc uses O_TMPFILE."
        ),
    ),
    cfg.StrOpt(
        "pbs_cache_dir",
        default="/pbs-tmp/cache",
        help="Directory used as XDG_CACHE_HOME for proxmox-backup-client.",
    ),
]


def register_opts(conf):
    conf.register_opts(pbs_opts, group="pbs_backup")
