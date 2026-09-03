"""Domain core: the id-centric data model that everything hangs on.

Every added file gets a unique history id; transcription, summary versions and
analysis versions are all linked to that id. This single source of truth is
what keeps statuses and artifacts from desynchronising over a long history.
"""
