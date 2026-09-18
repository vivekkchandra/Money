"""Private fixed bootstrap; not an operator CLI and never a source of qualification."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


def installed_files_fingerprint(environment: Path) -> str:
    """Stdlib bootstrap twin; compare with adapters.native_subprocess in tests."""
    if (environment.resolve() != environment.absolute()
            or not (environment / "pyvenv.cfg").is_file()
            or not (environment / "lib").is_dir()):
        raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
    entries = sorted((environment / "lib").rglob("*"))
    if len(entries) > 100_000 or any(path.is_symlink() for path in entries):
        raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
    files = [environment / "pyvenv.cfg", *(path for path in entries if path.is_file())]
    manifest = hashlib.sha256()
    total = 0
    for path in files:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        before = path.stat()
        total += before.st_size
        if before.st_size > 512_000_000 or total > 8_000_000_000:
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1_000_000):
                digest.update(block)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        manifest.update((digest.hexdigest() + "  " + path.relative_to(environment).as_posix() + "\n").encode())
    return manifest.hexdigest()


def main() -> int:
    # -I ignores user site/PYTHONPATH; -S prevents installed .pth startup hooks
    # from importing upstream code before Money's capability guard. Add only the
    # exact interpreter's installed packages, never editable checkout paths.
    repository = Path(__file__).resolve().parents[1]
    environment = Path(sys.executable).absolute().parents[1]
    packages = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if not (environment / "pyvenv.cfg").is_file() or not packages.is_dir():
        return 2
    # Python <=3.13 sets sys.prefix to its base under -S. Restore the actual venv
    # for both dependency lookup and the existing read-root capability guard.
    sys.prefix = str(environment)
    sys.exec_prefix = str(environment)
    sys.path.insert(0, str(packages))
    sys.path.insert(0, str(repository / "src"))
    workdir = Path.cwd().resolve()
    # Defence in depth if invoked directly: inherited provider/database values
    # have no role in this process. The single selected key arrives via stdin.
    os.environ.clear()
    os.environ.update({
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true",
        "DO_NOT_TRACK": "1", "CREWAI_TELEMETRY_ENABLED": "false",
        "CREWAI_TRACING_ENABLED": "false", "CREWAI_STORAGE_DIR": str(workdir),
        "TMPDIR": str(workdir), "XDG_CACHE_HOME": str(workdir / "cache"),
    })
    tempfile.tempdir = str(workdir)
    sys.dont_write_bytecode = True
    response_fd = os.dup(sys.stdout.fileno())
    with open(os.devnull, "w") as sink:
        # Redirect file descriptors as well as Python streams: native C output and
        # import-time dependency logging must never enter the JSON/secret channel.
        os.dup2(sink.fileno(), sys.stdout.fileno())
        os.dup2(sink.fileno(), sys.stderr.fileno())
        try:
            # Authenticate dependency bytes before importing even Pydantic. -S
            # also stops .pth files from executing before this integrity check.
            raw = sys.stdin.buffer.read(20_000_001)
            sys.stdin.close()
            if len(raw) > 20_000_000:
                return 2
            initial = json.loads(raw)
            if not isinstance(initial, dict):
                return 2
            expected_environment = initial.get("expected_environment_sha256")
            if (not isinstance(expected_environment, str) or len(expected_environment) != 64
                    or expected_environment != installed_files_fingerprint(environment)):
                return 2
            from money.adapters.native_subprocess import (
                execute_child_request,
            )
            from money.research.inference import strict_response_json

            request = strict_response_json(raw)
            result = execute_child_request(request, workdir, repository)
            while result:
                written = os.write(response_fd, result)
                result = result[written:]
            return 0
        except BaseException:
            # Never echo exception repr: config, prompts or dependencies may carry secrets.
            return 2
        finally:
            os.close(response_fd)


if __name__ == "__main__":
    raise SystemExit(main())
