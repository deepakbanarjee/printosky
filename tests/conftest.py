"""
Pytest configuration for Printosky tests.
Sets PRINTOSKY_DB to an in-memory path so no real DB is needed.
"""
import os
os.environ.setdefault("PRINTOSKY_DB", ":memory:")
