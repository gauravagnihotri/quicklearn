"""The six-stage ingest and generation pipeline (PRD §6).

acquire → normalize → extract → segment → filter → generate.

Each stage is a discrete, individually retryable job type. The stage functions here are pure
async callables; :mod:`quicklearn.handlers.ingest` wires them into the job queue.
"""
