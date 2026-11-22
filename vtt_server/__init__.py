"""FastAPI web service for vtt-transcribe.

Importing the package is cheap on purpose: the application (``vtt_server.app``) reads its secrets and database settings
when it is imported, so the ``vtt-server`` command must be able to start (and print ``--help``) before any of that is set.
"""
