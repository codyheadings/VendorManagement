"""The dashboard's Upload dialog. All logic lives in upload_service.py; this is only the screen.
"""
import hashlib
import streamlit as st
import notify
import services.upload_service as up


def _show_result(result) -> None:
    if hasattr(result, "summary"):                       # a vendor import report
        st.success("Vendor list imported.")
        st.code(result.summary())
        return
    ok = result.status in ("applied", "info_only")
    (st.success if ok else st.warning)(f"{result.status}: {result.message}")
    for note in result.notes:
        st.caption(f"- {note}")
    if result.status in ("invalid", "needs_vendor", "failed"):
        st.info("This needs a person. It is listed in the review queue.")


@st.dialog("Upload files", width="large")
def show_upload_dialog():
    left, right = st.columns(2)

    with left:
        label = st.selectbox("What are you uploading?", list(up.UPLOAD_KINDS), key="upload_label")
        kind, suffixes = up.UPLOAD_KINDS[label]
        pasting = False
        if kind in up.PASTE_KINDS:
            pasting = st.radio("Provide it as", ["File", "Pasted text"], horizontal=True, key=f"upload_mode_{kind}") == "Pasted text"
        uploaded, pasted = None, ""
        if pasting:
            pasted = st.text_area("Paste the text here", height=260, key=f"upload_text_{kind}",
                                  placeholder="From: ...\nDate: ...\nSubject: ...\n\nBody" if kind == "email" else None)
        else:
            # a separate key per type, so changing the dropdown never carries a wrong file over
            uploaded = st.file_uploader("File", type=[s.lstrip(".") for s in sorted(suffixes)], key=f"upload_file_{kind}")
            st.caption(f"Up to {up.MAX_BYTES // 1_000_000} MB. The type you choose decides how the file is read.")

    with right:
        st.subheader("Preview")
        if uploaded is None and not pasted.strip():
            st.info("Choose a file or paste text to see what will happen. Nothing is saved until you confirm.")
            return

        state_key = f"upload_state_{kind}"
        source_id = hashlib.sha256(pasted.encode()).hexdigest() if pasting else uploaded.file_id
        state = st.session_state.get(state_key)
        if state is None or state["file_id"] != source_id:            # stage and preview once per input
            try:
                if pasting:
                    path, notes = up.save_pasted(pasted, kind)
                else:
                    path, notes = up.save_upload(uploaded.name, uploaded.getvalue(), kind), []
                pre = up.preview(kind, path)
                pre.warnings = notes + pre.warnings
                state = {"file_id": source_id, "path": path, "preview": pre, "result": None}
            except up.UploadError as e:
                st.error(str(e))
                return
            st.session_state[state_key] = state

        pre = state["preview"]
        if state["result"] is not None:
            _show_result(state["result"])
            return

        st.markdown(f"**{pre.title}**" if pre.title else "")
        if pre.rows:
            st.table({label_: value for label_, value in pre.rows})
        for w in pre.warnings:
            st.warning(w)
        if not pre.ok:
            st.error("Nothing will be added." if pre.already_loaded else "This file cannot be added as it is.")
            return

        confirmed = True
        if pre.type_warning:
            st.warning(pre.type_warning)
            confirmed = st.checkbox("I checked: this is the right document type", key=f"upload_ok_{kind}_{source_id}")
        if pre.needs_ai:
            st.caption("This document will be extracted by artificial intelligence. Results are saved as pending until you approve them.")
        if st.button("Extract and add for review" if pre.needs_ai else "Confirm and add", type="primary",
                     use_container_width=True, key=f"upload_go_{kind}", disabled=not confirmed):
            with st.spinner("Working..."):
                try:
                    state["result"] = up.commit(kind, state["path"], allow_ai=pre.needs_ai, type_confirmed=confirmed)
                    try:                                    # tell Slack about new review items; never block the upload
                        notify.run_checks(("review",))
                    except Exception:
                        pass
                except Exception as e:
                    st.error(f"Could not add the file: {type(e).__name__}: {e}")
                    return
            st.rerun(scope="fragment")