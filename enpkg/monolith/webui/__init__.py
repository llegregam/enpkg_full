"""NiceGUI front end for the pipeline.

Imports nothing at package level, so ``import enpkg.monolith`` does not pull in NiceGUI
for callers that only want the pipeline. Modules here may import from
``enpkg.monolith.pipeline``; the reverse is forbidden, which is what keeps the pipeline
runnable from the ``enpkg`` command line with no GUI dependency installed.
"""
