"""Subprocess and logging helpers shared by the daemon and the forge adapters."""
import subprocess
import sys


def log(msg):
    """Failures go to stderr, which the service manager puts in its journal.

    Silence is why an intermittent `gh` failure could empty the whole sidebar
    with nothing to point at afterwards.
    """
    print(f"ci-tokens: {msg}", file=sys.stderr, flush=True)


def run(cmd, cwd=None, quiet=False):
    """stdout on success, None on failure.

    None is not "". The caller has to be able to tell a command that answered
    "nothing" from one that could not answer at all, because clearing every
    token on a transient failure is exactly what makes the rows collapse.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=120)
        if p.returncode != 0:
            if not quiet:
                log(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} rc={p.returncode}: "
                    f"{(p.stderr or '').strip()[:200]}")
            return None
        return p.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        if not quiet:
            log(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {e}")
        return None
