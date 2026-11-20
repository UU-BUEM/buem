Conda Setup
===========

Installing and configuring BuEM using Conda package manager.

Prerequisites
-------------

Make sure you have completed the :doc:`prerequisites` page before continuing.

* `Anaconda <https://docs.anaconda.com/anaconda/install/>`_ or `Miniconda <https://docs.conda.io/en/latest/miniconda.html>`_
* Python >= 3.14 (pinned in ``infrastructure/env/buem_env.yml``)

Quick Installation
------------------

Create the BuEM environment directly from the repository:

.. code-block:: bash

    # Clone the repository
    git clone https://github.com/UU-BUEM/buem.git
    cd buem
    
    # Create conda environment
    conda env create -f infrastructure/env/buem_env.yml
    
    # Activate environment
    conda activate buem_env
    
    # Ready to use! Run with:
    python -m src.buem.main

Building from Source (Advanced)
-------------------------------

To build and install with conda:

.. code-block:: bash

    conda install conda-build
    conda build .
    conda install --use-local buem

.. note::
   **Development Usage:** When using the conda environment with the source code, 
   always prefix commands with ``src.`` (e.g., ``python -m src.buem.main``) to 
   avoid import conflicts.

Manual Environment Setup
------------------------

For custom environment configuration:

.. code-block:: bash

    # Create new environment
    conda create -n buem_env python=3.14
    conda activate buem_env
    
    # Install core dependencies
    conda install -c conda-forge numpy pandas scipy matplotlib
    conda install -c conda-forge requests flask gunicorn

.. warning::

   buem is **not currently published to PyPI or a conda channel**
   (``pyproject.toml``'s own comment confirms this, same as its
   ``occupancy``/``weather`` optional dependencies). Always install from
   the repository: ``conda env create -f infrastructure/env/buem_env.yml``
   then ``conda activate buem_env`` and ``conda develop src`` (or
   ``pip install -e .``).

Optional Dependencies
---------------------

For development and documentation:

.. code-block:: bash

    # Documentation tools
    conda install -c conda-forge sphinx sphinx_rtd_theme
    
    # Development tools
    conda install -c conda-forge pytest pytest-cov black flake8
    
    # Jupyter for interactive analysis
    conda install -c conda-forge jupyter notebook

.. _gurobi-setup:

Optional: Gurobi Solver
-----------------------

BuEM runs without Gurobi. The default LP path uses CLARABEL, with OSQP as
a fallback, and both come with CVXPY in ``buem_env``. Gurobi is a separate
commercial solver that you can add to the environment. It needs a licence,
so it is not listed in ``buem_env.yml`` or ``pyproject.toml``.

.. important::

   The built-in solve paths in ``buem.thermal.model_buem`` do not select
   Gurobi. The LP path always tries CLARABEL and then OSQP. The MILP path
   looks only for CBC or GLPK_MI, then falls back to PuLP + CBC. Once
   installed, Gurobi is available to CVXPY for your own models
   (``prob.solve(solver=cp.GUROBI)``), but it does not change BuEM's
   results.

**1. Install the Optimizer and a licence.** Download Gurobi from
`gurobi.com <https://www.gurobi.com/downloads/>`__. Academic users can get
a free named-user licence and activate it with ``grbgetkey``. By default
this writes ``gurobi.lic`` to your home directory, where ``gurobipy``
finds it without further setup. If the file is somewhere else, set
``GRB_LICENSE_FILE`` to its full path.

**2. Install the Python package into** ``buem_env``. Match the major
version to your licence. A version-14 licence covers Gurobi 14 and
earlier releases.

.. code-block:: bash

    conda install -n buem_env -c gurobi "gurobi=14.0.0"

The ``gurobi`` channel publishes builds per Python version. Because
``buem_env`` uses Python 3.14, conda picks the ``py314`` build.

**3. Verify** the licence and the CVXPY interface:

.. code-block:: bash

    conda activate buem_env
    python -c "import gurobipy as gp; print(gp.gurobi.version())"
    python -c "import cvxpy as cp; print('GUROBI' in cp.installed_solvers())"

The first command prints the version tuple, for example ``(14, 0, 0)``.
When ``gurobipy`` first starts the solver it also prints the licence type
and expiry date. The second command prints ``True``.

.. note::

   **Pyomo is not required.** BuEM builds its models with CVXPY, plus PuLP
   on the MILP fallback path. CVXPY calls Gurobi through ``gurobipy``
   directly, so no extra modelling layer is needed.

Verifying Installation
----------------------

.. code-block:: bash

    # Test import
    python -c "import buem; print(buem.__version__)"
    
    # Check API server
    python -m buem.apis.api_server
    
    # Run tests
    pytest tests/

Troubleshooting
---------------

**Environment conflicts:**

.. code-block:: bash

    # Remove and recreate environment
    conda env remove -n buem_env
    conda env create -f infrastructure/env/buem_env.yml

**Module not found errors:**

.. code-block:: bash

    # Re-register the source tree with conda
    conda develop src

For additional help, check the container logs or API ``/api/health`` endpoint.