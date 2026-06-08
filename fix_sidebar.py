with open('views/unit_commonality.py', 'r') as f:
    code = f.read()

target = """def _render_sidebar_controls(rodb_cm_check: Any) -> List[Tuple[str, Any]]:
    \"\"\"Renders the sidebar layer selection and returns checked layers.\"\"\"
    if not rodb_cm_check or not rodb_cm_check.layers:
        return []
        
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🗺️ Commonality Layers")
    layer_names = sorted(rodb_cm_check.layers.keys())
    na_checked = []
    
    for ln in layer_names:
        lyr = rodb_cm_check.layers[ln]
        is_chk = st.sidebar.checkbox(
            f"{ln} ({lyr.layer_type})",
            value=st.session_state.get(f"vis_{ln}", False),
            key=f"vis_{ln}"
        )
        if is_chk:
            na_checked.append((ln, lyr))
            with st.sidebar.expander(f"Opacity: {ln}"):
                def_op = _layer_opacity(ln, lyr.layer_type, False)
                st.slider("Opacity", 0.0, 1.0, value=def_op, key=f"opacity_{ln}")
                
    st.sidebar.markdown("---")
    return na_checked"""

replacement = """def _render_sidebar_controls(rodb_cm_check: Any) -> List[Tuple[str, Any]]:
    \"\"\"Reads the sidebar layer selection from session state and returns checked layers.\"\"\"
    if not rodb_cm_check or not rodb_cm_check.layers:
        return []
        
    layer_names = sorted(rodb_cm_check.layers.keys())
    na_checked = []
    
    for ln in layer_names:
        lyr = rodb_cm_check.layers[ln]
        # The actual checkboxes are rendered in ui/sidebar.py. 
        # We just read the session state here.
        is_chk = st.session_state.get(f"vis_{ln}", False)
        if is_chk:
            na_checked.append((ln, lyr))
            
    return na_checked"""

code = code.replace(target, replacement)

with open('views/unit_commonality.py', 'w') as f:
    f.write(code)
