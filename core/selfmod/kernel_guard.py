"""Fail-closed safety-kernel guard (audit 2026-06-22, red-team verdict).

The eventual autonomous self-modifier MUST call ``assert_kernel_safe()`` on its
candidate change set and ABORT on any violation. Also runnable as a CLI
pre-commit / pre-push hook (``python -m core.selfmod.kernel_guard --staged``)
for defense-in-depth at the git layer.

FAIL-CLOSED: a missing/empty manifest, an unreadable path, or any doubt => the
path is treated as a violation. The guard and its manifest are themselves in the
protected set (core/selfmod/), so a self-modifier cannot weaken its own fence.
"""
from __future__ import annotations

import sys

try:
    from core.selfmod import kernel_manifest as _m
except Exception:  # manifest import failure => fail-closed (deny everything)
    _m = None


def _norm(p: str) -> str:
    """Repo-relative POSIX form: backslashes -> /, strip leading ./ and /."""
    s = str(p).replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def kernel_violations(changed_paths) -> list:
    """Return the kernel-violating paths in ``changed_paths``.

    Fail-closed: if the manifest is unavailable or empty, EVERY non-blank path is
    a violation (better to block a benign change than miss a kernel edit)."""
    paths = [_norm(p) for p in (changed_paths or []) if str(p).strip()]
    if _m is None:
        return [f"{p} (manifest unavailable — fail-closed)" for p in paths]
    protected = getattr(_m, "PROTECTED_PATHS", None)
    runtime = getattr(_m, "RUNTIME_STATE_DENY", None)
    prefixes = getattr(_m, "PROTECTED_PREFIXES", ()) or ()
    if not protected or not runtime:
        return [f"{p} (manifest empty — fail-closed)" for p in paths]
    dirs = tuple(d for d in protected if d.endswith("/"))
    out = []
    for p in paths:
        if (p in protected
                or p in runtime
                or any(p == d.rstrip("/") or p.startswith(d) for d in dirs)
                or any(p == pre or p.startswith(pre) for pre in prefixes)):
            out.append(p)
    return out


def assert_kernel_safe(changed_paths) -> tuple:
    """Return ``(ok, violations)``. ``ok`` is True only when NO changed path
    touches the immutable safety kernel."""
    v = kernel_violations(changed_paths)
    return (len(v) == 0, v)


def _staged_files() -> list:
    import subprocess
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--staged":
        try:
            paths = _staged_files()
        except Exception as e:  # git unavailable in a SUP run is itself suspicious
            sys.stderr.write(f"[kernel-guard] cannot read staged files ({e}) — fail-open for humans, "
                             "but the SUP must call assert_kernel_safe() directly.\n")
            return 0
        if not paths:
            return 0  # nothing staged
    else:
        paths = argv
    ok, v = assert_kernel_safe(paths)
    if not ok:
        sys.stderr.write(
            "[kernel-guard] BLOCKED — change touches the IMMUTABLE SAFETY KERNEL:\n"
            + "\n".join(f"  - {x}" for x in v)
            + "\nSL rails, mode/DRY_RUN gating, leverage caps, breakers, the promotion gate,\n"
              "the CONTROLLED_LIVE latch, and runtime control state are HUMAN-ONLY. A\n"
              "self-modifier must abort; a human edit is fine outside the SUP pipeline.\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
