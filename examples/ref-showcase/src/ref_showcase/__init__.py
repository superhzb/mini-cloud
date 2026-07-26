"""ref-showcase — the mini-cloud "kitchen sink" reference application.

A small *Document Intelligence* service (upload → chunk & store → queue-driven embed + summarize →
semantic search + per-document chat) whose only job is to exercise **every public method of every
SDK package** threaded into one believable domain. Where ``ref-fastapi`` is the lean 7/7 template
seed, ``ref-showcase`` is the exhaustive surface that touches each SDK symbol first — so an SDK gap
or signature drift breaks *here* before it reaches downstream apps (the coverage/regression canary).

It carries **no** bespoke persistence, object store, queue, or inference client — that absence is
the platform acceptance criterion. It must hold **7/7 on the scorecard**: richer, not sloppier.

See ``docs/`` for the per-service tour. Build state is staged; ``docs/build-status.md`` tracks it.
"""

__version__ = "0.1.0"
