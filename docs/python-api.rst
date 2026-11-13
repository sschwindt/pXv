Python API
==========

Configuration and processing
----------------------------

.. automodule:: pxv.project
   :members: Project, VideoCfg, AnalysisCfg, GeometryCfg, GroundTruthCfg, load_project, save_project

.. automodule:: pxv.pipeline
   :members: RunResult, run

Geometry and motion
-------------------

.. automodule:: pxv.geometry.world
   :members:

.. automodule:: pxv.velocimetry.piv
   :members: PIVField, piv_pair, piv_ensemble

.. automodule:: pxv.velocimetry.ptv
   :members: Tracks, run_ptv

Ground truth and exports
------------------------

.. automodule:: pxv.io.groundtruth
   :members: GroundTruth, read_groundtruth

.. automodule:: pxv.calibrate.profile
   :members: Conversion, convert_to_surface, two_point_surface

.. automodule:: pxv.export.datasets
   :members:
