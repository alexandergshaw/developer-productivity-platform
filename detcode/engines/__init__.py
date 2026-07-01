"""Deterministic engines.

Each engine is a pure function of an Intent plus source. Verticals are added
here in order: rewrite (refactors/codemods) first, then scaffold, synth, and
repair.
"""
