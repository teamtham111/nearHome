"""Runtime schemas for data received from external providers and snapshots.

These schemas intentionally live beside the adapters rather than in
``app.schemas``.  The latter contains NearHome's public HTTP contracts; this
package validates data crossing into NearHome from systems it does not
control.
"""
