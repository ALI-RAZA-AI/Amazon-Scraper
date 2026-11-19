"""Frontend: streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="Amazon Dataset Builder", page_icon=":material/dataset:", layout="wide")

pages = [
    st.Page("ui/page_scrape.py", title="Scrape", icon=":material/download:", default=True),
    st.Page("ui/page_data.py", title="Browse data", icon=":material/table_view:"),
    st.Page("ui/page_schema.py", title="Data structure", icon=":material/schema:"),
    st.Page("ui/page_export.py", title="Export CSV", icon=":material/file_download:"),
    st.Page("ui/page_settings.py", title="Settings", icon=":material/settings:"),
]
st.navigation(pages).run()
