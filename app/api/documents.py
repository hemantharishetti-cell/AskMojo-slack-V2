"""
Document CRUD routes — re-exports from the existing vector_logic/routes.py.

This module imports the existing router which contains /upload, /documents,
/search, /refine-description endpoints.  The /ask endpoint is now served
by api/ask.py via the new pipeline.

During the incremental migration, both the old routes.py and the new
api/ask.py are mounted.  The old /ask endpoint in routes.py remains
available as a fallback.
"""

from __future__ import annotations

# The existing router already contains all document CRUD endpoints.
# We simply re-export it here so main.py can mount it under the new
# api/ package if desired.  No changes needed to the original file.
from app.vector_logic.routes import router  # noqa: F401
