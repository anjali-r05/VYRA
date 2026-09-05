"""
Shared SQLAlchemy instance.

This module exists to avoid circular imports: models import `db` from here,
and app.py initializes `db` with the Flask app.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()