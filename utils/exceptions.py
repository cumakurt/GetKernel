"""Application-specific exceptions (English messages for logs and code)."""


class GetKernelError(Exception):
    """Base exception for GetKernel."""


class SystemCheckError(GetKernelError):
    """Environment or platform check failed."""


class DependencyError(GetKernelError):
    """Missing or broken system packages."""


class DownloadError(GetKernelError):
    """Kernel source or metadata download failed."""


class VerificationError(GetKernelError):
    """Checksum or signature verification failed."""


class ConfigError(GetKernelError):
    """Kernel Kconfig handling failed."""


class CompilationError(GetKernelError):
    """Kernel build failed."""


class InstallationError(GetKernelError):
    """dpkg/apt installation failed."""


class SecurityError(GetKernelError):
    """Unsafe URL or path."""


class ChecksumMismatchError(VerificationError):
    """SHA256 or other hash mismatch."""
