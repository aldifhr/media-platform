"""Test bootstrap.

The migrated manhwa config (modules/manhwa/config.py) has a production boot
guard that demands secrets unless ENVIRONMENT=development. Tests exercise pure
logic, so bypass the guard here before any manhwa module is imported.
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")
