"""The dashboard's Upload dialog. All logic lives in upload_service.py; this is only the screen.
"""
import streamlit as st

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


@st.dialog("Upload files", width=1440)
def show_upload_dialog():
    left, right = st.columns(2)

    with left:
        label = st.selectbox("What are you uploading?", list(up.UPLOAD_KINDS), key="upload_label")
        kind, suffixes = up.UPLOAD_KINDS[label]
        # a separate key per type, so changing the dropdown never carries a wrong file over
        uploaded = st.file_uploader("File", type=[s.lstrip(".") for s in sorted(suffixes)], key=f"upload_file_{kind}")
        st.caption(f"Up to {up.MAX_BYTES // 1_000_000} MB. The type you choose decides how the file is read.")

    with right:
        st.subheader("Preview")
        if uploaded is None:
            st.info("Choose a file to see what will happen. Nothing is saved until you confirm.")
            return

        state_key = f"upload_state_{kind}"
        state = st.session_state.get(state_key)
        if state is None or state["file_id"] != uploaded.file_id:     # stage and preview once per file
            try:
                path = up.save_upload(uploaded.name, uploaded.getvalue(), kind)
                state = {"file_id": uploaded.file_id, "path": path, "preview": up.preview(kind, path), "result": None}
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

        if pre.needs_ai:
            st.caption("This makes one AI call to read the document. Results are saved as pending until you approve them.")
        if st.button("Extract and add for review" if pre.needs_ai else "Confirm and add", type="primary",
                     use_container_width=True, key=f"upload_go_{kind}"):
            with st.spinner("Working..."):
                try:
                    state["result"] = up.commit(kind, state["path"], allow_ai=pre.needs_ai)
                except Exception as e:  # noqa: BLE001  never show a stack trace; say what failed
                    st.error(f"Could not add the file: {type(e).__name__}: {e}")
                    return
            st.rerun(scope="fragment")