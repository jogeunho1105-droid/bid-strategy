# Public read-only strategy board — 2026-10-22

## User-authorized scope
The user requested no password, publication on request, strategy and supporting rationale only, and removal of old analysis/admin menu items. This is intentional public viewing. Only the specifically approved result snapshot is added to the public repository. Full workbooks, raw histories, input files, private storage links and credentials are not included.

## Implementation
- Keep deployment entrypoint `입찰 앱.py` and existing requirements.
- Explicitly route only to the read-only strategy/rationale board. No login, uploader, admin form, prediction, collection or bidding-submission endpoint.
- Preserve the former application byte-identically under `legacy/입찰_앱_20261022_보관.py`. The former authenticated publication page remains in source but is not registered by st.navigation. Keep the calculation-engine files unchanged.
- Read `publications/latest.json` on every rerun. Approved future result snapshots can replace this file through authenticated repository writes; viewers need no password or manual upload. No timed schedule, watcher or credential bypass is introduced.
- Display explicit analysis/data/deadline dates, historical-result and expired-deadline warnings.
- Residential supervision has one recommendation. Held cases expose no actionable numeric recommendation. This display rule does not rewrite the original workbook or calculation engine.
- Amounts are copied as conditional arithmetic estimates, not final bid amounts.

## Publication source
Approved 2026-10-22 v2, eight notices for 2026-10-23. Source workbook SHA-256 is recorded in the snapshot. Only selected strategy/rationale fields are published; the full workbook stays private. Public repository history can retain an older snapshot even after a later update.

## Validation and limitations
33 local standard-library data/safety/mock-UI tests passed. One real Streamlit AppTest was skipped because Streamlit is not installed in the execution container; attempted installation failed at network name resolution. The test-file upload was blocked and was not retried; no new CI workflow is deployed. No hosted-production UI success is claimed. No full strategy backtest was rerun for this display-only change.

## Recovery
Prior main commit: `eeb2af8e008b3a9c706cd261a9a61b369c34895c`. Old entrypoint is preserved byte-identically in legacy. No raw ledger deletion or update. Previous UI tests target the former interface; this change intentionally replaces that interface.
