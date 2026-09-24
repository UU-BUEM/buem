import os

# Work around a Windows-only crash: this environment's numpy build links
# MKL, and something else already loaded in the same process (CVXPY's
# bundled solvers) loads its own copy of the Intel OpenMP runtime -- two
# OpenMP runtimes active in one process is a known, silent
# access-violation hazard. Any BLAS matrix-matrix call (np.dot on 2D
# arrays, np.cov, np.corrcoef; plain vector dot/mean/std are unaffected)
# kills the interpreter outright with no Python exception, just a bare
# process exit. Forcing MKL onto its sequential (non-OpenMP) threading
# layer avoids the conflict. Must be set before numpy's own first import
# in the process (MKL reads it lazily on first BLAS call, but something
# in numpy/pandas's own import already appears to lock the choice in --
# buem.env.load_env() runs too late, since e.g. cfg_attribute.py imports
# pandas before calling it) -- this is the literal first statement
# evaluated on `import buem`, ahead of every submodule.
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

# Version: single source of truth is the git tag.
# Fallback chain: setuptools-scm (live from git) → _version.py → hardcoded fallback.
# The live git read avoids stale _version.py when using 'conda develop'.
try:
    from setuptools_scm import get_version as _scm_version
    __version__ = _scm_version()
except (ImportError, LookupError):
    # ImportError: setuptools-scm not installed. LookupError: not a git
    # checkout / no tags found (setuptools-scm's own failure mode).
    try:
        from buem._version import version as __version__
    except ImportError:
        __version__ = "0.1.3"  # keep in sync with pyproject.toml fallback_version
