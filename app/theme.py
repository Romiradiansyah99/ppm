"""T&T brand theme for the single-page PPM chat."""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {
  --bg: #FFFFFF;
  --sidebar: #F2EEE7;
  --text: #1E4479;
  --ink: #0B1F3A;
  --muted: #788FAF;
  --border: #D2DAE4;
  --bubble: #E9F5FC;
  --accent: #33A6E3;
  --primary: #1E4479;
}
html, body, [class*="css"] {
  font-family: "Inter", "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 15px;
  letter-spacing: -0.005em;
  -webkit-font-smoothing: antialiased;
}
.stApp { background: var(--bg); color: var(--ink); }
header, footer, #MainMenu, [data-testid="stToolbar"], [data-testid="stDecoration"] {
  visibility: hidden; height: 0;
}
.block-container {
  max-width: 820px;
  padding-top: 1.6rem;
  padding-bottom: 8rem;
}
h1, h2, h3 {
  color: var(--primary);
  font-weight: 600;
  letter-spacing: -0.02em;
}
[data-testid="stSidebar"] {
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  width: 260px !important;
}
[data-testid="stSidebar"] .block-container {
  padding-top: 1rem; padding-left: 1rem; padding-right: 1rem;
}
.msg-row.user {
  display: flex;
  justify-content: flex-end;
  margin: 10px 0 6px;
}
.msg-row.user .bubble {
  background: var(--bubble);
  color: var(--ink);
  border-radius: 18px;
  padding: 10px 14px;
  max-width: 80%;
  white-space: pre-wrap;
  line-height: 1.55;
  font-size: 15px;
}
[class*="st-key-assistant-"] { margin: 8px 0 20px; }
[class*="st-key-assistant-"] [data-testid="stMarkdownContainer"] p {
  line-height: 1.65;
  margin-bottom: .45rem;
}
[class*="st-key-assistant-"] [data-testid="stMarkdownContainer"] p:last-child {
  margin-bottom: 0;
}
[data-testid="stChatInput"] {
  border: 1px solid var(--border);
  border-radius: 24px;
  background: #FFFFFF;
  box-shadow: 0 2px 6px rgba(30, 68, 121, .06);
  padding: .25rem .55rem;
}
[data-testid="stChatInput"] textarea {
  background: transparent;
  color: var(--ink);
  font-family: "Inter", "Segoe UI", sans-serif;
}
.stButton > button {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #FFFFFF;
  color: var(--primary);
  font-size: 14px;
  font-family: "Inter", "Segoe UI", sans-serif;
  padding: .4rem .9rem;
}
.stButton > button:hover {
  background: var(--sidebar);
  color: var(--primary);
  border-color: var(--muted);
}
.stButton > button[kind="primary"],
.stButton > button[kind="primaryFormSubmit"] {
  background: var(--primary);
  border-color: var(--primary);
  color: #FFFFFF;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[kind="primaryFormSubmit"]:hover {
  background: #16345C;
  border-color: #16345C;
  color: #FFFFFF;
}
.stDownloadButton > button {
  border: 1px solid var(--primary);
  border-radius: 8px;
  background: #FFFFFF;
  color: var(--primary);
}
[data-testid="stExpander"] {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #FFFFFF;
}
[data-testid="stDataFrame"] { border: 1px solid var(--border); }
.stAlert { border-radius: 8px; }
[data-testid="stForm"] { border: none; padding: 0; }
a { color: var(--accent); }
.login-title {
  text-align: center;
  font-size: 26px;
  font-weight: 600;
  color: var(--primary);
  margin-top: 16vh;
  margin-bottom: 1.2rem;
}
.login-hint {
  text-align: center;
  color: var(--muted);
  font-size: 12px;
  margin-top: .8rem;
}
.empty-title {
  text-align: center;
  font-size: 26px;
  font-weight: 600;
  color: var(--primary);
  margin-top: 18vh;
  margin-bottom: 1.5rem;
}
.side-label {
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
  margin: 1rem 0 .35rem;
}
.side-user {
  font-size: 13px;
  color: var(--primary);
  font-weight: 600;
}
.side-meta {
  font-size: 11px;
  color: var(--muted);
}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
