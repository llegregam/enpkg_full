"""The ``enpkg`` command line.

Each subcommand is a thin adapter over :mod:`enpkg.monolith.pipeline`: it parses
arguments, builds validated configs, calls the same runner the GUI calls, and renders
the result. No orchestration logic lives here.
"""
