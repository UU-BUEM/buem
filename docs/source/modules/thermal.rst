thermal — 5R1C Thermal Model
============================

:Source: ``buem/thermal/model_buem.py``

Purpose
-------

Implements the **ISO 13790 5R1C** (five-resistor, one-capacitor) hourly
thermal model.  Given a building envelope, weather, and occupancy it computes
annual heating and cooling demand by solving a constrained optimisation
problem.

5R1C Network
------------

Three temperature nodes are connected by thermal conductances:

- **T_air** — interior air
- **T_sur** — internal surfaces
- **T_m** — thermal mass

External air temperature **T_e** (from weather) and the HVAC heat flow
**Q_HC** (the decision variable) close the network.

Three energy-balance equations per time-step enforce the physics:

1. *Air-node balance* — convective coupling to surfaces and ventilation.
2. *Surface-node balance* — radiative coupling to mass and windows.
3. *Mass-node dynamics* — forward-Euler integration with **annual-periodic
   wrap-around** (:math:`T_m(n{-}1) \to T_m(0)`), so no initial-condition
   guess is needed.

Gain Distribution (ISO 13790 §C.2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Internal and solar gains are split across nodes:

.. math::

   \phi_{ia} &= 0.5\,\phi_{int} \\
   \phi_{st} &= (1 - f_{Am} - f_w)\,(0.5\,\phi_{int} + \phi_{sol}) \\
   \phi_m    &= f_{Am}\,(0.5\,\phi_{int} + \phi_{sol})

where :math:`f_{Am} = A_m / A_{tot}` and :math:`f_w` is the window-area
fraction.

Solar Gains
~~~~~~~~~~~

Plane-of-array (POA) irradiance per surface element is computed with
**pvlib** (isotropic-sky diffuse model).

- **Window gains**: :math:`Q_{win} = \text{POA} \times A_{win} \times g_{gl} \times (1-F_f) \times F_w`
- **Opaque gains**: :math:`Q_{opaque} = \alpha \times R_{se} \times U \times A \times \text{POA}`

The :math:`R_{se} \times U` factor ensures that only the ~4–7 % of absorbed
solar energy that actually penetrates inward is counted; without it cooling
loads are vastly over-estimated.

Solver
------

**LP (default)** — Minimises :math:`\sum |Q_{\text{HC}}|` subject to the
3 × 8760 physics constraints plus comfort dead-band
:math:`T_{lb} \le T_{air} \le T_{ub}`.
Solved via CVXPY using CLARABEL (preferred) with OSQP fallback.
Positive :math:`Q_{\text{HC}}` = heating, negative = cooling.

**MILP (experimental)** — Separates heating :math:`Q_h \ge 0` and cooling
:math:`Q_c \ge 0` with binary indicators and big-M constraints.
Solved via CVXPY (CBC / GLPK_MI) or PuLP + CBC.

Neither path selects a commercial solver. Installing Gurobi makes
``cp.GUROBI`` available to CVXPY, but it does not change which solver
these two paths use. See :ref:`gurobi-setup`.

Comfort Bounds & Setback
~~~~~~~~~~~~~~~~~~~~~~~~

``comfortT_lb``/``comfortT_ub`` accept either a scalar (applied to every
timestep, the default) or a per-timestep ``pd.Series`` aligned with the
weather index — letting a building express a real occupied/unoccupied
setpoint schedule (e.g. a school closed nights/weekends/summer) directly,
rather than only via the coarse annual ``F_red_htr`` scalar below.
Internally both forms are normalized to a length-``n`` array immediately,
so every downstream constraint/big-M/``T_set`` computation is
array-shaped regardless of which form the caller used.

``F_red_htr`` (ISO 13790 §13.2.2) reduces effective transmission
conductances to approximate night/weekend setback *without* varying the
comfort bounds themselves — TABULA's residential convention:
``F_red_htr1 = 0.95`` (AB/MFH) or ``0.90`` (SFH/TH); ``1.0`` (default) =
no reduction. It remains the simpler option for ordinary residential
night setback; the time-varying comfort-bound form above is the better
fit for buildings with genuinely irregular occupancy.

Sanity bounds widened to admit non-residential use: ``h_room`` up to
20 m (was 5 m — blocked warehouses/sports halls/industrial halls);
``comfortT_lb``/``comfortT_ub`` within [5, 35] °C (was [15, 30] — blocked
frost-protection-only setpoints for lightly-conditioned industrial/
warehouse space).

Key Methods
-----------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Method
     - Role
   * - ``__init__``
     - Initialise model, declare containers
   * - ``_initEnvelop()``
     - Parse ``components`` tree → U-values, areas, conductances **H** [kW/K]
   * - ``_init5R1C()``
     - Compute network parameters (A_f, A_m, C_m, H_is, H_ms, H_win); build solar-gain profiles via pvlib
   * - ``_calcRadiation()``
     - POA irradiance per element (kW/m²)
   * - ``_addConstraints()``
     - Assemble sparse 3n × 4n equality matrix
   * - ``sim_model()``
     - Build and solve LP or MILP
   * - ``_readResults()``
     - Populate ``detailedResults`` DataFrame (T_air, T_m, T_sur, Q_HC, gains)

Design Note — LP vs. Parameterisation
--------------------------------------

Earlier BuEM versions separated heating and cooling via rule-based
parameterisation.  The current LP formulation unifies them into a single
decision variable :math:`Q_{\text{HC}}` whose sign determines the mode.
This removes ad-hoc heuristics and produces sparse (zero-in-dead-band)
profiles that match expected HVAC behaviour.
