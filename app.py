import re
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="GZ PMO Portal (Demo)", layout="wide")

# -----------------------------
# Constants / Column Mapping
# -----------------------------
SHEET_NAME = None  # default first sheet
PC_TYPES = {"P", "C"}  # Only P/C go to PPT portal views per your rule. 【1-6e9386】

# Excel columns observed in your file (best-effort mapping).
COL_TYPE = "Cat."
COL_NAME = "Name"
COL_DESC = "Description"
COL_STATUS = "Status"
COL_START = "Start date"
COL_END = "End date"
COL_BUDGET_RMB = "Budget (RMB)"
COL_BUDGET_CODE = "Budget"
COL_BUDGET_APPROVAL = "Budget Approval"
COL_GTS_PM = "GTS PM"
COL_BIZ_PM = "Business PM \nor Keyuser"
COL_SPONSOR = "Sponser"
COL_FOLLOWUP = "Follow up action"

# -----------------------------
# Helpers
# -----------------------------
def norm_type(x: str) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip().upper()

def safe_str(x):
    if pd.isna(x):
        return ""
    return str(x)

def parse_pct_from_text(text: str):
    """
    Try to parse an overall percent from free text like:
    'Overall Progress (~60%)' (appears in PPT). 【2-46f3c3】
    """
    if not text:
        return None
    m = re.search(r"(\d{1,3})\s*%+", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    return None

def rag_icon(status_text: str) -> str:
    # Map to On Track / Minor / Significant (PPT semantics). 【2-46f3c3】
    s = (status_text or "").strip().lower()
    if "on track" in s or "on-track" in s or "ontrack" in s:
        return "🟢"
    if "minor" in s or "delay" in s or "risk" in s:
        return "🟡"
    if "significant" in s or "stalled" in s:
        return "🔴"
    # Excel statuses like "In Progress", "Pending", "Completed" etc. 【1-6e9386】
    if "completed" in s:
        return "✅"
    if "pending" in s:
        return "⏳"
    if "in progress" in s:
        return "🟦"
    return "•"

def to_ontrack_bucket(status_text: str) -> str:
    """
    Normalize status into PPT bucket:
    - On Track / Minor / Significant
    """
    s = (status_text or "").strip().lower()
    if "significant" in s:
        return "Significant"
    if "minor" in s:
        return "Minor"
    # Heuristic: In Progress defaults to On Track unless user sets otherwise
    return "On Track"

@st.cache_data(show_spinner=False)
def load_excel_to_df(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_excel(file_bytes, engine="openpyxl", sheet_name=SHEET_NAME)
    return df

def ensure_session():
    if "page" not in st.session_state:
        st.session_state.page = "portfolio"
    if "selected_key" not in st.session_state:
        st.session_state.selected_key = None
    if "updates" not in st.session_state:
        # key -> dict of module fields
        st.session_state.updates = {}

def record_update(key: str, payload: dict):
    st.session_state.updates[key] = {
        **payload,
        "_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def merged_view(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Excel baseline with in-app updates (session_state).
    """
    df2 = df.copy()
    # Create a stable key: Type + Name (works well for demo)
    df2["_key"] = df2[COL_TYPE].apply(norm_type) + " | " + df2[COL_NAME].astype(str)
    # Apply updates
    for k, u in st.session_state.updates.items():
        mask = df2["_key"] == k
        if mask.any():
            # Update common fields shown on PPT-like page
            if "status_bucket" in u:
                df2.loc[mask, COL_STATUS] = u["status_bucket"]
            if "followup_action" in u:
                df2.loc[mask, COL_FOLLOWUP] = u["followup_action"]
            # We keep risks/mitigation/escalations in portal only (not in original Excel columns)
            # but we can store them in additional columns for export.
            for extra_col in ["_risk_mitigation", "_escalations", "_scope", "_progress_text",
                              "_budget_approved", "_budget_committed", "_budget_spent", "_budget_forecast", "_budget_variance",
                              "_updated_at"]:
                if extra_col not in df2.columns:
                    df2[extra_col] = ""
            df2.loc[mask, "_risk_mitigation"] = u.get("risk_mitigation", "")
            df2.loc[mask, "_escalations"] = u.get("escalations", "")
            df2.loc[mask, "_scope"] = u.get("scope", "")
            df2.loc[mask, "_progress_text"] = u.get("progress_text", "")
            df2.loc[mask, "_budget_approved"] = u.get("budget_approved", "")
            df2.loc[mask, "_budget_committed"] = u.get("budget_committed", "")
            df2.loc[mask, "_budget_spent"] = u.get("budget_spent", "")
            df2.loc[mask, "_budget_forecast"] = u.get("budget_forecast", "")
            df2.loc[mask, "_budget_variance"] = u.get("budget_variance", "")
            df2.loc[mask, "_updated_at"] = u.get("_updated_at", "")
    return df2

# -----------------------------
# UI Components (PPT-like detail)
# -----------------------------
def render_detail_ppt_layout(row: pd.Series, key: str):
    """
    1:1 layout concept aligned to your PPT one-page template: 【2-46f3c3】
    - Title
    - Left: Follow-up Action Plan
    - Right: Risks & Mitigation + Escalations
    - Scope / Overall Progress
    - Budget Review table
    - Timeline (simplified)
    """
    # Pull baseline from Excel 【1-6e9386】
    t = norm_type(row.get(COL_TYPE, ""))
    name = safe_str(row.get(COL_NAME, ""))
    sponsor = safe_str(row.get(COL_SPONSOR, ""))
    gts_pm = safe_str(row.get(COL_GTS_PM, ""))
    biz_pm = safe_str(row.get(COL_BIZ_PM, ""))
    status_raw = safe_str(row.get(COL_STATUS, ""))

    desc = safe_str(row.get(COL_DESC, ""))
    followup = safe_str(row.get(COL_FOLLOWUP, ""))

    start = safe_str(row.get(COL_START, ""))
    end = safe_str(row.get(COL_END, ""))
    budget_rmb = safe_str(row.get(COL_BUDGET_RMB, ""))
    budget_code = safe_str(row.get(COL_BUDGET_CODE, ""))
    budget_approval = safe_str(row.get(COL_BUDGET_APPROVAL, ""))

    # Merge in updates if exist
    u = st.session_state.updates.get(key, {})
    status_bucket = u.get("status_bucket", to_ontrack_bucket(status_raw))
    risk_mitigation = u.get("risk_mitigation", "")
    escalations = u.get("escalations", "")
    scope = u.get("scope", desc)  # default scope uses Description from Excel 【1-6e9386】
    progress_text = u.get("progress_text", "")
    pct_guess = parse_pct_from_text(progress_text) if progress_text else None

    budget_approved = u.get("budget_approved", budget_rmb)
    budget_committed = u.get("budget_committed", "")
    budget_spent = u.get("budget_spent", "")
    budget_forecast = u.get("budget_forecast", "")
    budget_variance = u.get("budget_variance", "")

    # Header
    st.markdown(f"## {t} - {name}")
    st.caption(f"Sponsor: **{sponsor}** | GTS PM: **{gts_pm}** | Business PM/Keyuser: **{biz_pm}**")
    st.markdown(f"**Status:** {rag_icon(status_bucket)} **{status_bucket}**")

    st.divider()

    # Middle area: two columns like PPT (left action plan, right risks)
    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.markdown("### 🟦 Follow-up Action Plan")
        st.info(followup if followup else "—")
        st.markdown("### 📋 Project Scope")
        st.write(scope if scope else "—")

    with col_right:
        st.markdown("### 🟥 Risks & Mitigation Plan")
        st.warning(risk_mitigation if risk_mitigation else "—")
        st.markdown("### 🚨 Escalations")
        st.error(escalations if escalations else "—")

    st.divider()

    # Overall progress block (PPT has a narrative block) 【2-46f3c3】
    st.markdown("### 📈 Overall Progress")
    if progress_text:
        st.write(progress_text)
    else:
        st.write("—")
    if pct_guess is not None:
        st.progress(pct_guess / 100.0, text=f"~{pct_guess}%")

    st.divider()

    # Budget Review (table) 【2-46f3c3】
    st.markdown("### 💰 Budget Review")
    budget_df = pd.DataFrame(
        [
            ["Approved Budget", budget_approved],
            ["Project Committed", budget_committed],
            ["Spent to Date", budget_spent],
            ["Expect Final Cost (Forecast)", budget_forecast],
            ["Variance", budget_variance],
            ["Budget Code", budget_code],
            ["Budget Approval", budget_approval],
        ],
        columns=["Item", "Value"]
    )
    st.dataframe(budget_df, use_container_width=True, hide_index=True)

    # Timeline (simplified) - your PPT has milestone grid; for demo we show start/end + a simple bar.
    st.divider()
    st.markdown("### 📅 Timeline (Simplified)")
    st.write(f"Start: **{start}**  |  End/Go-live: **{end}**")
    st.caption("（PPT里为 Milestone 网格/甘特，此处 Demo 用简化展示，后续可替换为真正甘特组件。）")

    st.divider()
    # Navigation
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("← Back to Portfolio", use_container_width=True):
            st.session_state.page = "portfolio"
            st.session_state.selected_key = None
            st.rerun()
    with c2:
        if st.button("✏ Update This Item", type="primary", use_container_width=True):
            st.session_state.page = "update"
            st.rerun()

def render_update_form(row: pd.Series, key: str):
    # baseline
    status_raw = safe_str(row.get(COL_STATUS, ""))
    followup = safe_str(row.get(COL_FOLLOWUP, ""))
    desc = safe_str(row.get(COL_DESC, ""))
    budget_rmb = safe_str(row.get(COL_BUDGET_RMB, ""))

    u = st.session_state.updates.get(key, {})
    default_status = u.get("status_bucket", to_ontrack_bucket(status_raw))
    default_followup = u.get("followup_action", followup)
    default_risk = u.get("risk_mitigation", "")
    default_escal = u.get("escalations", "")
    default_scope = u.get("scope", desc)
    default_progress = u.get("progress_text", "")
    default_budget_approved = u.get("budget_approved", budget_rmb)
    default_budget_committed = u.get("budget_committed", "")
    default_budget_spent = u.get("budget_spent", "")
    default_budget_forecast = u.get("budget_forecast", "")
    default_budget_variance = u.get("budget_variance", "")

    st.markdown("## ✏ Update (PPT Modules)")
    st.caption("这里的更新是按 PPT 模块拆分（Action/Risk/Scope/Progress/Budget），不是随便写一段文字。【2-46f3c3】")

    with st.form("update_form", clear_on_submit=False):
        status_bucket = st.selectbox("Status (On Track / Minor / Significant)", ["On Track", "Minor", "Significant"],
                                     index=["On Track", "Minor", "Significant"].index(default_status) if default_status in ["On Track", "Minor", "Significant"] else 0)

        st.markdown("### 🟦 Follow-up Action Plan")
        followup_action = st.text_area("Action Plan", value=default_followup, height=120)

        st.markdown("### 🟥 Risks & Mitigation + 🚨 Escalations")
        risk_mitigation = st.text_area("Risks & Mitigation", value=default_risk, height=120)
        escalations = st.text_area("Escalations", value=default_escal, height=80)

        st.markdown("### 📋 Project Scope")
        scope = st.text_area("Scope", value=default_scope, height=120)

        st.markdown("### 📈 Overall Progress (Narrative)")
        progress_text = st.text_area("Progress narrative (you can keep the PPT style, e.g. 'Overall Progress (~60%) ...')",
                                     value=default_progress, height=150)

        st.markdown("### 💰 Budget Review")
        colb1, colb2, colb3, colb4, colb5 = st.columns(5)
        with colb1:
            budget_approved = st.text_input("Approved", value=default_budget_approved)
        with colb2:
            budget_committed = st.text_input("Committed", value=default_budget_committed)
        with colb3:
            budget_spent = st.text_input("Spent", value=default_budget_spent)
        with colb4:
            budget_forecast = st.text_input("Forecast", value=default_budget_forecast)
        with colb5:
            budget_variance = st.text_input("Variance", value=default_budget_variance)

        submitted = st.form_submit_button("✅ Save Update", type="primary")

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("← Cancel / Back", use_container_width=True):
            st.session_state.page = "detail"
            st.rerun()
    with c2:
        if submitted:
            record_update(key, {
                "status_bucket": status_bucket,
                "followup_action": followup_action,
                "risk_mitigation": risk_mitigation,
                "escalations": escalations,
                "scope": scope,
                "progress_text": progress_text,
                "budget_approved": budget_approved,
                "budget_committed": budget_committed,
                "budget_spent": budget_spent,
                "budget_forecast": budget_forecast,
                "budget_variance": budget_variance,
            })
            st.success("已保存（Demo阶段：先保存在系统内存/会话中，后续再决定写回Excel还是系统内写入）。")
            st.session_state.page = "detail"
            st.rerun()

# -----------------------------
# Main App
# -----------------------------
ensure_session()

st.sidebar.title("GZ PMO Portal (Demo)")
st.sidebar.caption("本地运行用于会议演示（避免外网URL被安全策略拦截）。")

# Load data
st.sidebar.markdown("### Data Source")
uploaded = st.sidebar.file_uploader("Upload Excel (Project Details bi-weekly update)", type=["xlsx"])
use_sample = st.sidebar.checkbox("Use local file name if exists", value=True)

df = None
error = None

try:
    if uploaded is not None:
        df = load_excel_to_df(uploaded)
    else:
        if use_sample:
            # user can place the Excel in same folder as app.py for demo
            # filename matches your provided artifact
            local_path = "Project Details bi-weekly update 2026.xlsx"
            df = pd.read_excel(local_path, engine="openpyxl", sheet_name=SHEET_NAME)
        else:
            df = None
except Exception as e:
    error = str(e)
    df = None

if df is None:
    st.warning("请在左侧上传 Excel，或将 'Project Details bi-weekly update 2026.xlsx' 放在 app.py 同目录。")
    if error:
        st.code(error)
    st.stop()

# Basic sanity: keep only the weekly project update tab rows (sheet_1 in your file). 【1-6e9386】
# If the Excel has multiple sheets/sections, we still attempt to work on visible columns.
missing_cols = [c for c in [COL_TYPE, COL_NAME] if c not in df.columns]
if missing_cols:
    st.error(f"Excel 缺少必要列：{missing_cols}. 请确认使用的是 Project Details bi-weekly update 文件。")
    st.stop()

# Merge updates into view
df_view = merged_view(df)
df_view["_type_norm"] = df_view[COL_TYPE].apply(norm_type)
df_view["_key"] = df_view["_type_norm"] + " | " + df_view[COL_NAME].astype(str)

# Sidebar filters
st.sidebar.markdown("### Filters")
show_only_pc = st.sidebar.checkbox("Only show Type P / C (PPT scope)", value=True)
type_filter = st.sidebar.multiselect("Type", sorted(df_view["_type_norm"].unique().tolist()),
                                    default=["P", "C"] if show_only_pc else sorted(df_view["_type_norm"].unique().tolist()))
status_filter = st.sidebar.multiselect("Status", sorted(df_view[COL_STATUS].dropna().astype(str).unique().tolist()),
                                      default=[])

# Apply filters
filtered = df_view[df_view["_type_norm"].isin(type_filter)]
if status_filter:
    filtered = filtered[filtered[COL_STATUS].astype(str).isin(status_filter)]

# Routing
if st.session_state.page == "portfolio":
    st.markdown("## 📋 Portfolio (Excel-like)")
    st.caption("默认按你的规则只看 P/C（进入PPT汇报范围）；I 类型可在侧边栏放开查看。【1-6e9386】")

    # Display minimal portfolio columns aligned with your Excel fields. 【1-6e9386】
    show_cols = []
    for c in [COL_TYPE, COL_NAME, COL_STATUS, COL_START, COL_END, COL_BUDGET_RMB, COL_GTS_PM, COL_BIZ_PM, COL_SPONSOR]:
        if c in filtered.columns:
            show_cols.append(c)

    # Add update marker
    filtered_display = filtered.copy()
    filtered_display["Updated?"] = filtered_display["_key"].apply(lambda k: "✅" if k in st.session_state.updates else "")
    display_cols = ["Updated?"] + show_cols

    st.dataframe(filtered_display[display_cols], use_container_width=True, hide_index=True)

    st.markdown("### Open a Project / Change (Detail)")
    options = filtered["_key"].tolist()
    if not options:
        st.info("当前筛选条件下没有数据。")
    else:
        chosen = st.selectbox("Select item", options)
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("Open Detail", type="primary", use_container_width=True):
                st.session_state.selected_key = chosen
                st.session_state.page = "detail"
                st.rerun()
        with c2:
            if st.button("Open Update", use_container_width=True):
                st.session_state.selected_key = chosen
                st.session_state.page = "update"
                st.rerun()
        with c3:
            st.caption("（会议演示建议：先Open Detail展示PPT布局，再点Update演示周会更新流程。）")

    st.divider()
    st.markdown("## ⬇ Export (Demo)")
    st.caption("Demo阶段：更新先保存在系统会话中；可以导出一份带更新字段的Excel供后续对比/讨论。")
    export_df = df_view.copy()
    export_bytes = None
    try:
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="PMO_Portal_Export")
        export_bytes = buf.getvalue()
    except Exception as e:
        st.warning(f"导出失败：{e}")

    if export_bytes:
        st.download_button("Download Export Excel", data=export_bytes,
                           file_name="PMO_Portal_Export.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif st.session_state.page == "detail":
    if not st.session_state.selected_key:
        st.session_state.page = "portfolio"
        st.rerun()

    key = st.session_state.selected_key
    row_df = df_view[df_view["_key"] == key]
    if row_df.empty:
        st.error("找不到选中的条目（可能筛选条件变化导致）。")
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()

    row = row_df.iloc[0]
    render_detail_ppt_layout(row, key)

elif st.session_state.page == "update":
    if not st.session_state.selected_key:
        st.session_state.page = "portfolio"
        st.rerun()

    key = st.session_state.selected_key
    row_df = df_view[df_view["_key"] == key]
    if row_df.empty:
        st.error("找不到选中的条目（可能筛选条件变化导致）。")
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()

    row = row_df.iloc[0]
    render_update_form(row, key)

else:
    st.session_state.page = "portfolio"
    st.rerun()
