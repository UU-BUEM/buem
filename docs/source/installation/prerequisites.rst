.. _prerequisites:

Setup & Prerequisites
=====================

This page lists all software requirements needed to work with BuEM.
Requirements are separated by role: **Users** interact with BuEM through its
API (typically via Docker), while **Developers** work directly with the source
code and contribute to the project.

.. contents:: On this page
   :local:
   :depth: 2

Software Requirements
---------------------

The table below summarises every piece of software you may need.
Items marked **compulsory** must be installed before you can run BuEM.

.. list-table::
   :header-rows: 1
   :widths: 20 14 14 32 20

   * - Requirement
     - Version
     - Compulsory / Optional
     - Use / Notes
     - Download Link
   * - Python
     - >= 3.14
     - Compulsory
     - Runtime for BuEM. Pinned in ``infrastructure/env/buem_env.yml``.
     - `python.org <https://www.python.org/downloads/>`__
   * - Conda (Miniconda or Anaconda)
     - latest
     - Compulsory (developers)
     - Environment & dependency management. Miniconda is sufficient.
     - `Miniconda <https://docs.conda.io/en/latest/miniconda.html>`__
   * - Docker Desktop
     - >= 4.6
     - Compulsory (users)
     - Runs the BuEM container. Includes Docker Engine >= 29 and Compose v2.
     - `Docker Desktop <https://www.docker.com/products/docker-desktop/>`__
   * - Git
     - latest
     - Compulsory
     - Source code access and version control.
     - `git-scm.com <https://git-scm.com/downloads>`__
   * - PostgreSQL
     - >= 17
     - Compulsory
     - Database back-end for building data.
     - `PostgreSQL <https://www.postgresql.org/download/>`__
   * - PgAdmin
     - >= 8
     - Optional
     - GUI for inspecting and managing the PostgreSQL database.
     - `PgAdmin <https://www.pgadmin.org/download/>`__
   * - City2TABULA
     - v0.5.0
     - Compulsory
     - Populates the building database that BuEM reads.
       See :ref:`city2tabula-setup` below.
     - `City2TABULA setup <https://github.com/THD-Spatial-AI/city2tabula/blob/main/docs/installation/setup.md>`__
   * - IDE (VS Code / PyCharm)
     - latest
     - Optional
     - Recommended editor for development. VS Code with the Python
       extension works well.
     - `VS Code <https://code.visualstudio.com/>`__ /
       `PyCharm <https://www.jetbrains.com/pycharm/>`__
   * - Gurobi Optimizer
     - 14.0
     - Optional
     - Commercial LP/MILP solver, free for academic use. It is not
       needed to run BuEM. See :ref:`gurobi-setup`.
     - `Gurobi <https://www.gurobi.com/downloads/>`__

.. note::

   **Docker Desktop >= 4.6** ships with Docker Engine >= 29 and Docker
   Compose v2.  You do not need to install Docker Engine or Compose
   separately when using Docker Desktop.

For Users
---------

If you only need to **run BuEM simulations via its API**, install the
following and skip the developer section:

1. **Docker Desktop** – provides the container runtime.
2. **Git** – to clone the repository and access ``docker-compose.yml``.
3. **PostgreSQL** – database for building parameters.
4. **City2TABULA** – populates the database with building archetypes.

Quick verification:

.. code-block:: bash

   docker --version        # Docker version 29.x or higher
   docker compose version  # Docker Compose v2.x
   git --version
   pg_config --version     # PostgreSQL 17.x or higher

.. tip::

   ``pg_config`` is used instead of ``psql`` because many PostgreSQL
   installations (especially GUI-based installers on Windows) do not add
   ``psql`` to the system PATH.  ``pg_config`` ships with the core
   PostgreSQL libraries on all platforms.  If the command is not found,
   ensure the PostgreSQL installation directory (e.g.
   ``C:\Program Files\PostgreSQL\17\bin`` on Windows, or
   ``/usr/bin`` on Linux) is on your ``PATH``.

Once the prerequisites are in place, proceed to :doc:`docker_installation` to
start the BuEM container.

For Developers
--------------

Developers need everything listed for users **plus** the local Python
environment:

1. **Python 3.14** and **Conda** – managed via
   ``infrastructure/env/buem_env.yml``.
2. **IDE** – VS Code or PyCharm recommended.
3. **PgAdmin** – optional, helpful for database inspection.

Quick verification:

.. code-block:: bash

   python --version        # Python 3.14.x
   conda --version
   docker --version        # Docker version 29.x or higher
   git --version
   pg_config --version     # PostgreSQL 17.x or higher

See the tip in the Users section above if ``pg_config`` is not found.

Proceed to :doc:`conda_setup` to create the development environment, then see
:doc:`development_setup` for code-quality tools and testing.

.. _city2tabula-setup:

City2TABULA
-----------

`City2TABULA <https://github.com/THD-Spatial-AI/city2tabula>`__ generates the
building-archetype database that BuEM consumes.  Follow the
`City2TABULA setup guide
<https://github.com/THD-Spatial-AI/city2tabula/blob/main/docs/installation/setup.md>`__
to install version **v0.5.0** and populate your PostgreSQL database before
running BuEM.

Key steps (summary):

1. Clone the City2TABULA repository.
2. Install its conda environment.
3. Configure the PostgreSQL connection.
4. Run the database population script.

Refer to the `City2TABULA documentation
<https://github.com/THD-Spatial-AI/city2tabula/blob/main/docs/installation/setup.md>`__
for the full procedure.

System Requirements
-------------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Resource
     - Minimum
   * - Operating System
     - Windows 10+, macOS 12+, or Linux (Ubuntu 22.04+ recommended)
   * - Memory
     - 4 GB (8 GB recommended for large datasets)
   * - Disk Space
     - 2 GB (additional space for weather data and results)
   * - Network
     - Internet access for pulling Docker images and downloading dependencies

Next Steps
----------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Role
     - Next Page
   * - Users (API / Docker)
     - :doc:`docker_installation`
   * - Developers
     - :doc:`conda_setup`
