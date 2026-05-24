class PbsBackupError(Exception):
    """Raised when proxmox-backup-client returns non-zero or unexpected output."""


class RbdHelperError(Exception):
    """Raised when an rbd / rbd-nbd shell helper fails."""
