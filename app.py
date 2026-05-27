import re
import io
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime

# =============================
# Page config
# =============================
st.set_page_config(page_title="GZ PMO Portal (Local Demo)", layout="wide")

# =============================
# Excel column mapping (from your template)
# =============================
SHEET_NAME = None  # default: first sheet

# Only P/C are in PPT scope (P=Project, C=Change). 
PC_TYPES = {"P", "C"}

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


# =============================
# Helpers
# =============================
def norm_type(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip().upper()


def safe_str(x) -> str:
    if pd.isna(x):
        return ""
    return str(x)


def to_ontrack_bucket(status_text: str) -> str:
    """
    Normalize status into PPT semantics: On Track / Minor / Significant. 
    Demo mapping:
      - if text contains 'significant' -> Significant
      - if contains 'minor' -> Minor
      - else -> On Track
    """
    s = (status_text or "").strip().lower()
    if "significant" in s:
        return "Significant"
    if "minor" in s:
        return "Minor"
    return "On Track"


def rag_icon(status_bucket: str) -> str:
    s = (status_bucket or "").strip().lower()
    if "on track" in s:
        return "🟢"
    if "minor" in s:
        return "🟡"
    if "significant" in s:
        return "🔴"
    # fallback for raw excel statuses 
    if "completed" in s:
        return "✅"
    if "pending" in s:
        return "⏳"
    if "in progress" in s:
        return "🟦"
    return "•"


def parse_pct_from_text(text: str):
    """Parse percent from narrative like 'Overall Progress (~60%)' in PPT style. """
    if not text:
        return None
    m = re.search(r"(\d{1,3})\s*%+", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    return None


def _parse_date_fuzzy(x):
    """
    Parse Excel date values like 01/01/2026, 2026.12.10, Q2 2026, NA/TBD. 
    """
    if x is None:
        return pd.NaT
    if isinstance(x, float) and np.isnan(x):
        return pd.NaT

    s = str(x).strip()
    if s == "" or s.lower() in {"na", "n/a", "tbd", "none"}:
        return pd.NaT

    # Quarter formats: "Q2 2026"
    m = re.match(r"^Q([1-4])\s*([12]\d{3})$", s, flags=re.IGNORECASE)
    if m:
        q = int(m.group(1))
        y = int(m.group(2))
        month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
        return pd.Timestamp(year=y, month=month, day=1)

    # Standard parsing
    return pd.to_datetime(s, errors="coerce", dayfirst=False)


@st.cache_data(show_spinner=False)
def load_excel_to_df(file_bytes: bytes) -> pd.DataFrame:
    return pd.read_excel(file_bytes, engine="openpyxl", sheet_name=SHEET_NAME)


def ensure_session():
    if "page" not in st.session_state:
        st.session_state.page = "dashboard"  # Dashboard as landing
    if "selected_key" not in st.session_state:
        st.session_state.selected_key = None
    if "updates" not in st.session_state:
        st.session_state.updates = {}  # key -> dict


def record_update(key: str, payload: dict):
    st.session_state.updates[key] = {
        **payload,
        "_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def merged_view(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Excel baseline with in-app updates (session_state).
    Strategy unchanged: Demo stage keeps updates in-session + exportable. 
    """
    df2 = df.copy()
    df2["_type_norm"] = df2[COL_TYPE].apply(norm_type)
    df2["_key"] = df2["_type_norm"] + " | " + df2[COL_NAME].astype(str)

    # Ensure extra columns exist for export
    for extra_col in [
        "_risk_mitigation", "_escalations", "_scope", "_progress_text",
        "_budget_approved", "_budget_committed", "_budget_spent", "_budget_forecast", "_budget_variance",
        "_updated_at"
    ]:
        if extra_col not in df2.columns:
            df2[extra_col] = ""

    # Apply updates
    for k, u in st.session_state.updates.items():
        mask = df2["_key"] == k
        if mask.any():
            # Update status & followup back onto view
            if "status_bucket" in u:
                df2.loc[mask, COL_STATUS] = u["status_bucket"]
            if "followup_action" in u:
                df2.loc[mask, COL_FOLLOWUP] = u["followup_action"]

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


# =============================
# Dashboard: Metrics + Health + Gantt Timeline
# =============================
def compute_dashboard_metrics(df_view: pd.DataFrame):
    """
    Mirrors PPT summary intent: Total / On-going / Risk + Health dims. 
    Demo risk rule: Minor + Significant counted as risk (since Excel doesn't have risk severity field).
    """
    scoped = df_view[df_view["_type_norm"].isin(PC_TYPES)].copy()  # P/C only 
    total = len(scoped)

    status_lower = scoped[COL_STATUS].astype(str).str.lower()
    ongoing = (status_lower.str.contains("in progress")) | (status_lower.isin(["on track", "minor", "significant"]))
    ongoing_count = int(ongoing.sum())

    bucket = scoped[COL_STATUS].apply(to_ontrack_bucket)
    risk_count = int(bucket.isin(["Minor", "Significant"]).sum())

    updated_count = int(scoped["_key"].isin(st.session_state.updates.keys()).sum())

    return total, ongoing_count, risk_count, updated_count


def build_timeline_gantt_df(df_view: pd.DataFrame) -> pd.DataFrame:
    """
    Build Gantt dataframe from Excel Start/End, filtered to P/C only. 
    """
    scoped = df_view[df_view["_type_norm"].isin(PC_TYPES)].copy()
    if COL_START not in scoped.columns or COL_END not in scoped.columns:
        return pd.DataFrame(columns=["Name", "Type", "Start", "End", "StatusBucket"])

    scoped["Start"] = scoped[COL_START].apply(_parse_date_fuzzy)
    scoped["End"] = scoped[COL_END].apply(_parse_date_fuzzy)

    scoped = scoped.dropna(subset=["Start", "End"])
    if scoped.empty:
        return pd.DataFrame(columns=["Name", "Type", "Start", "End", "StatusBucket"])

    scoped["Type"] = scoped["_type_norm"]
    scoped["Name"] = scoped[COL_NAME].astype(str)
    scoped["StatusBucket"] = scoped[COL_STATUS].apply(to_ontrack_bucket)

    out = scoped[["Name", "Type", "Start", "End", "StatusBucket"]].copy()
    out = out.sort_values(["Start", "End", "Type", "Name"], ascending=[True, True, True, True])
    return out


def render_dashboard(df_view: pd.DataFrame):
    st.markdown("## 🏠 Dashboard（总览页）")
    st.caption("总览结构对齐 PPT 总体状态页意图：Total / On-going / Risk + Health 维度。")

    total, ongoing_count, risk_count, updated_count = compute_dashboard_metrics(df_view)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total (P/C)", total)
    c2.metric("On-going (P/C)", ongoing_count)
    c3.metric("Risk (Demo)", risk_count)
    c4.metric("Updated in Portal (session)", updated_count)

    st.divider()

    st.markdown("### ✅ Health Overview（与PPT维度一致）")
    st.caption("Demo阶段先对齐维度与布局；后续你定数据模型后再接入真实计算口径。")
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.info("Schedule Health")
    h2.info("Budget Health")
    h3.info("Scope Health")
    h4.info("Resource Health")
    h5.info("Quality Health")

    # -------- Timeline Gantt band module --------
    st.divider()
    st.markdown("### 📅 Timeline（Gantt条带总览）")
    st.caption("自动读取 Excel 的 Start date / End date 生成（仅 P/C）。用于呼应PPT底部Milestone/Timeline展示逻辑。")

    gantt_df = build_timeline_gantt_df(df_view)

    if gantt_df.empty:
        st.info("没有足够的起止日期可生成甘特（请确认 P/C 行有 Start date 与 End date）。")
    else:
        top_n = st.slider("显示条数（Top N）", min_value=5, max_value=min(60, len(gantt_df)), value=min(20, len(gantt_df)))
        show = gantt_df.head(top_n)

        spec = {
            "data": {"values": show.to_dict(orient="records")},
            "mark": {"type": "bar", "cornerRadius": 2},
            "encoding": {
                "y": {"field": "Name", "type": "nominal", "sort": "-x", "axis": {"title": ""}},
                "x": {"field": "Start", "type": "temporal", "axis": {"title": "Start"}},
                "x2": {"field": "End"},
                "color": {
                    "field": "StatusBucket",
                    "type": "nominal",
                    "scale": {
                        "domain": ["On Track", "Minor", "Significant"],
                        "range": ["#2E7D32", "#F9A825", "#C62828"]
                    },
                    "legend": {"title": "Status"}
                },
                "tooltip": [
                    {"field": "Type", "type": "nominal", "title": "Type"},
                    {"field": "Name", "type": "nominal", "title": "Name"},
                    {"field": "StatusBucket", "type": "nominal", "title": "Status"},
                    {"field": "Start", "type": "temporal", "title": "Start"},
                    {"field": "End", "type": "temporal", "title": "End"}
                ]
            },
            "height": {"step": 18}
        }

        st.vega_lite_chart(spec, use_container_width=True)

        with st.expander("查看甘特数据（表格）"):
            st.dataframe(show, use_container_width=True, hide_index=True)

    # -------- Attention list --------
    st.divider()
    st.markdown("### 🚨 Attention List（演示用）")
    scoped = df_view[df_view["_type_norm"].isin(PC_TYPES)].copy()
    scoped["StatusBucket"] = scoped[COL_STATUS].apply(to_ontrack_bucket)
    scoped["NeedUpdate?"] = scoped["_key"].apply(lambda k: "✅" if k in st.session_state.updates else "⏳")
    attention = scoped[(scoped["StatusBucket"].isin(["Minor", "Significant"])) | (scoped["NeedUpdate?"] == "⏳")].copy()

    cols = [COL_TYPE, COL_NAME, COL_STATUS, "StatusBucket", "NeedUpdate?"]
    cols = [c for c in cols if c in attention.columns]
    if len(attention) == 0:
        st.success("当前没有需要关注的条目（按演示口径）。")
    else:
        st.dataframe(attention[cols], use_container_width=True, hide_index=True)

    st.divider()
    if st.button("➡ Go to Portfolio（进入项目列表）", type="primary", use_container_width=True):
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()


# =============================
# Detail (1:1 PPT layout) + Update
# =============================
def render_detail_ppt_layout(row: pd.Series, key: str):
    """
    Detail page is layout-driven to match PPT one-page template sections. 
    """
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

    u = st.session_state.updates.get(key, {})
    status_bucket = u.get("status_bucket", to_ontrack_bucket(status_raw))
    risk_mitigation = u.get("risk_mitigation", "")
    escalations = u.get("escalations", "")
    scope = u.get("scope", desc)  # default scope uses Excel description 
    progress_text = u.get("progress_text", "")
    pct_guess = parse_pct_from_text(progress_text) if progress_text else None

    budget_approved = u.get("budget_approved", budget_rmb)
    budget_committed = u.get("budget_committed", "")
    budget_spent = u.get("budget_spent", "")
    budget_forecast = u.get("budget_forecast", "")
    budget_variance = u.get("budget_variance", "")

    st.markdown(f"## {t} - {name}")
    st.caption(f"Sponsor: **{sponsor}** | GTS PM: **{gts_pm}** | Business PM/Keyuser: **{biz_pm}**")
    st.markdown(f"**Status:** {rag_icon(status_bucket)} **{status_bucket}**")
    st.divider()

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
    st.markdown("### 📈 Overall Progress")
    st.write(progress_text if progress_text else "—")
    if pct_guess is not None:
        st.progress(pct_guess / 100.0, text=f"~{pct_guess}%")

    st.divider()
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

    st.divider()
    st.markdown("### 📅 Timeline (Simplified)")
    st.write(f"Start: **{start}**  |  End/Go-live: **{end}**")
    st.caption("（PPT里为 Milestone 网格/甘特；Demo 用简化展示。）")

    st.divider()
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
    st.caption("更新按 PPT 模块拆分（Action/Risk/Scope/Progress/Budget），保持与一页PPT一致。")

    with st.form("update_form", clear_on_submit=False):
        status_bucket = st.selectbox(
            "Status (On Track / Minor / Significant)",
            ["On Track", "Minor", "Significant"],
            index=["On Track", "Minor", "Significant"].index(default_status) if default_status in ["On Track", "Minor", "Significant"] else 0
        )

        st.markdown("### 🟦 Follow-up Action Plan")
        followup_action = st.text_area("Action Plan", value=default_followup, height=120)

        st.markdown("### 🟥 Risks & Mitigation + 🚨 Escalations")
        risk_mitigation = st.text_area("Risks & Mitigation", value=default_risk, height=120)
        escalations = st.text_area("Escalations", value=default_escal, height=80)

        st.markdown("### 📋 Project Scope")
        scope = st.text_area("Scope", value=default_scope, height=120)

        st.markdown("### 📈 Overall Progress (Narrative)")
        progress_text = st.text_area(
            "Progress narrative (keep PPT style e.g. 'Overall Progress (~60%) ...')",
            value=default_progress,
            height=150
        )

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
            st.success("已保存（Demo阶段：保存在会话中；后续再决定写回Excel或系统内写入）。")
            st.session_state.page = "detail"
            st.rerun()


# =============================
# App bootstrap
# =============================
ensure_session()

st.sidebar.title("GZ PMO Portal (Local Demo)")
st.sidebar.caption("本地运行用于会议演示（避免外网URL被安全策略拦截）。")

# Navigation
st.sidebar.markdown("### Navigation")
nav = st.sidebar.radio("Go to", ["Dashboard", "Portfolio"], index=0)
target = "dashboard" if nav == "Dashboard" else "portfolio"
if st.session_state.page != target and st.sidebar.button("Open", use_container_width=True):
    st.session_state.page = target
    st.session_state.selected_key = None
    st.rerun()

# Data Source
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
            # Put [Project Details bi-weekly update 2026.xlsx](https://beigeneo365apc.sharepoint.com/sites/MFGGZM/IT/GTS%20TechOps%20General/004_Project%20%26%20Delivery/001_Project%20Management%20Framework/01-GZ%20PMO/01-Project%20Management/Project%20Details%20bi-weekly%20update%202026.xlsx?web=1&EntityRepresentationId=b1ab1579-e942-4c00-92f2-af9de12e847e) next to app.py for demo. 
            local_path = "Project Details bi-weekly update 2026.xlsx"
            df = pd.read_excel(local_path, engine="openpyxl", sheet_name=SHEET_NAME)
except Exception as e:
    error = str(e)
    df = None

if df is None:
    st.warning("请在左侧上传 Excel，或将 [Project Details bi-weekly update 2026.xlsx](https://beigeneo365apc.sharepoint.com/sites/MFGGZM/IT/GTS%20TechOps%20General/004_Project%20%26%20Delivery/001_Project%20Management%20Framework/01-GZ%20PMO/01-Project%20Management/Project%20Details%20bi-weekly%20update%202026.xlsx?web=1&EntityRepresentationId=b1ab1579-e942-4c00-92f2-af9de12e847e) 放在 app.py 同目录。")
    if error:
        st.code(error)
    st.stop()

# Validate required columns
missing_cols = [c for c in [COL_TYPE, COL_NAME] if c not in df.columns]
if missing_cols:
    st.error(f"Excel 缺少必要列：{missing_cols}. 请确认使用的是 [Project Details bi-weekly update 2026.xlsx](https://beigeneo365apc.sharepoint.com/sites/MFGGZM/IT/GTS%20TechOps%20General/004_Project%20%26%20Delivery/001_Project%20Management%20Framework/01-GZ%20PMO/01-Project%20Management/Project%20Details%20bi-weekly%20update%202026.xlsx?web=1&EntityRepresentationId=b1ab1579-e942-4c00-92f2-af9de12e847e)。")
    st.stop()

# Merge updates
df_view = merged_view(df)

# Filters
st.sidebar.markdown("### Filters")
show_only_pc = st.sidebar.checkbox("Only show Type P / C (PPT scope)", value=True)
type_filter_default = ["P", "C"] if show_only_pc else sorted(df_view["_type_norm"].unique().tolist())
type_filter = st.sidebar.multiselect("Type", sorted(df_view["_type_norm"].unique().tolist()), default=type_filter_default)

status_filter = st.sidebar.multiselect(
    "Status",
    sorted(df_view[COL_STATUS].dropna().astype(str).unique().tolist()),
    default=[]
)

filtered = df_view[df_view["_type_norm"].isin(type_filter)]
if status_filter:
    filtered = filtered[filtered[COL_STATUS].astype(str).isin(status_filter)]

# Routing
if st.session_state.page == "dashboard":
    render_dashboard(filtered)

elif st.session_state.page == "portfolio":
    st.markdown("## 📋 Portfolio (Excel-like)")
    st.caption("默认按规则只看 P/C（进入PPT汇报范围）；I 类型可在侧边栏放开查看。")

    show_cols = []
    for c in [COL_TYPE, COL_NAME, COL_STATUS, COL_START, COL_END, COL_BUDGET_RMB, COL_GTS_PM, COL_BIZ_PM, COL_SPONSOR]:
        if c in filtered.columns:
            show_cols.append(c)

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
    st.caption("Demo阶段：更新先保存在系统会话中；可导出带更新字段的Excel供会后讨论。")

    export_df = df_view.copy()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="PMO_Portal_Export")
    export_bytes = buf.getvalue()

    st.download_button(
        "Download Export Excel",
        data=export_bytes,
        file_name="PMO_Portal_Export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

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
    st.session_state.page = "dashboard"
    st.rerun()