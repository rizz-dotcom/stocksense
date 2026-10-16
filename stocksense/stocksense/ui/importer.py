"""Reusable CSV import widget: upload -> preview -> explicit commit."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from stocksense.config import PROJECT_ROOT
from stocksense.services import import_service
from stocksense.ui.common import action, get_config, show_error


def render_import(db, user, kind: str) -> None:
    cfg = get_config()
    cols = import_service.COLUMNS[kind]
    st.caption("Required columns, exactly: " + ", ".join(f"`{c}`" for c in cols)
               + ". Dates: YYYY-MM-DD. Decimals use a dot (12.50). The whole file is "
                 "validated first; nothing is saved unless every row is valid.")
    sample = Path(PROJECT_ROOT) / "sample_data" / f"{kind}.csv"
    if sample.exists():
        st.download_button(f"Download example {kind}.csv", sample.read_bytes(),
                           file_name=f"example_{kind}.csv", mime="text/csv",
                           key=f"sample_{kind}")
    upload = st.file_uploader(f"Upload {kind} CSV", type=["csv"], key=f"upload_{kind}")
    if upload is None:
        return
    data = upload.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    cache_key = f"preview_{kind}"
    cached = st.session_state.get(cache_key)
    if not cached or cached[0] != digest:
        try:
            preview = import_service.preview_import(db, user, kind, data,
                                                    max_bytes=cfg.max_upload_bytes)
            st.session_state[cache_key] = (digest, preview, None)
        except Exception as exc:  # noqa: BLE001 - displayed and logged by show_error
            st.session_state[cache_key] = (digest, None, exc)
    _, preview, error = st.session_state[cache_key]
    if error is not None:
        show_error(error)
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", preview.total_rows)
    c2.metric("Valid", len(preview.accepted))
    c3.metric("Rejected", len(preview.rejected))
    c4.metric("Updates existing", preview.update_count)
    if preview.rejected:
        st.error("Some rows are invalid, so nothing will be imported. Fix them and upload again.")
        st.dataframe(pd.DataFrame([{"Line": r["line"], "Problem": r["error"]}
                                   for r in preview.rejected]), hide_index=True, width="stretch")
    if preview.accepted:
        with st.expander(f"Preview valid rows ({len(preview.accepted)})",
                         expanded=not preview.rejected):
            rows = [{k: v for k, v in r.items() if not k.startswith("_") and k != "product_id"}
                    | {"action": r["_status"]} for r in preview.accepted]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if preview.can_commit:
        if preview.update_count:
            st.warning(f"{preview.update_count} existing record(s) will be updated. "
                       "Each change is recorded in the audit log.")
        if st.button(f"Import {len(preview.accepted)} row(s)", type="primary",
                     key=f"commit_{kind}"):
            with action():
                summary = import_service.commit_import(db, user, kind, data,
                                                       max_bytes=cfg.max_upload_bytes)
                st.session_state.pop(cache_key, None)
                st.success(f"Imported: {summary['new']} new, {summary['updated']} updated, "
                           f"{summary['unchanged']} unchanged.")
