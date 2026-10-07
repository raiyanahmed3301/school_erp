"""Vercel entrypoint for the School ERP Flask application."""

from schoolerp import create_app

app = create_app()

