import re
import io
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="GZ PMO Portal (Local Demo)", layout="wide")

# =============================
# Excel mapping
# =============================
# ✅ 关键修复：sheet_name=0 才表示“第一个sheet”
SHEET_NAME = 0

PC_TYPES = {"P", "C"}  # only P/C are in PPT scope 

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
    if "completed" in s:
        return "✅"
    if "pending" in s:
        return "⏳"
    if "in progress" in s:
        return "🟦"
    return "•"


def parse_pct_from_text(text: str):
    if not text:
        return None
    m = re.search(r"(\d{1,3})\s*%+", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    return None


def _parse_date_fuzzy(x):
    if x is None:
        return pd.NaT
    if isinstance(x, float) and np.isnan(x):
        return pd.NaT

    s = str(x).strip()
    if s == "" or s.lower() in {"na", "n/a", "tbd", "none"}:
        return pd.NaT

    m = re.match(r"^Q([1-4])\s*([12]\d{3})$", s, flags=re.IGNORECASE)
    if m:
        q = int(m.group(1))
        y = int(m.group(2))
        month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
        return pd.Timestamp(year=y, month=month, day=1)

    return pd.to_datetime(s, errors="coerce", dayfirst=False)


@st.cache_data(show_spinner=False)
def load_excel_smart(file_obj) -> pd.DataFrame:
    """
    ✅ 关键修复：避免 pandas 返回 dict 导致 df.columns 报错
    - 先读第一个sheet（sheet_name=0）
    - 若返回 dict（某些情况下），自动挑含 Cat./Name 的sheet
    """
    obj = pd.read_excel(file_obj, engine="openpyxl", sheet_name=SHEET_NAME)

    # 如果意外读成 dict（比如别处把 sheet_name=None），做兜底
    if isinstance(obj, dict):
        for _, d in obj.items():
            if isinstance(d, pd.DataFrame) and (COL_TYPE in d.columns) and (COL_NAME in d.columns):
                return d
        # fallback: 取第一张
        return list(obj.values())[0]

    return obj


def ensure_session():
    if "page" not in st.session_state:
        st.session_state.page = "dashboard"
    if "selected_key" not in st.session_state:
        st.session_state.selected_key = None
    if "updates" not in st.session_state:
        st.session_state.updates = {}


def record_update(key: str, payload: dict):
    st.session_state.updates[key] = {
        **payload,
        "_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def merged_view(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    df2["_type_norm"] = df2[COL_TYPE].apply(norm_type)
    df2["_key"] = df2["_type_norm"] + " | " + df2[COL_NAME].astype(str)

    for extra_col in [
        "_risk_mitigation", "_escalations", "_scope", "_progress_text",
        "_budget_approved", "_budget_committed", "_budget_spent", "_budget_forecast", "_budget_variance",
        "_updated_at"
    ]:
        if extra_col not in df2.columns:
            df2[extra_col] = ""

    for k, u in st.session_state.updates.items():
        mask = df2["_key"] == k
        if mask.any():
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
# Dashboard
# =============================
def compute_dashboard_metrics(df_view: pd.DataFrame):
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
    st.caption("总览结构对齐PPT总体状态页意图：Total / On-going / Risk + Health维度。")

    total, ongoing_count, risk_count, updated_count = compute_dashboard_metrics(df_view)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total (P/C)", total)
    c2.metric("On-going (P/C)", ongoing_count)
    c3.metric("Risk (Demo)", risk_count)
    c4.metric("Updated in Portal (session)", updated_count)

    st.divider()
    st.markdown("### ✅ Health Overview（与PPT维度一致）")
    st.caption("Demo阶段先对齐结构；后续你定数据口径后再接入真实计算。")
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.info("Schedule Health")
    h2.info("Budget Health")
    h3.info("Scope Health")
    h4.info("Resource Health")
    h5.info("Quality Health")

    st.divider()
    st.markdown("### 📅 Timeline（Gantt条带总览）")
    st.caption("从Excel的 Start/End 自动生成（仅P/C），呼应PPT底部Timeline展示。")

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

    st.divider()
    if st.button("➡ Go to Portfolio（进入项目列表）", type="primary", use_container_width=True):
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()


# =============================
# Detail (PPT layout) + Update
# =============================
def render_detail_ppt_layout(row: pd.Series, key: str):
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
    scope = u.get("scope", desc)
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
    st.caption("更新按PPT模块拆分（Action/Risk/Scope/Progress/Budget），保持一页PPT一致。")

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
# Bootstrap
# =============================
ensure_session()

st.sidebar.title("GZ PMO Portal (Local Demo)")
st.sidebar.caption("本地运行用于会议演示（避免外网URL被安全策略拦截）。")

st.sidebar.markdown("### Navigation")
nav = st.sidebar.radio("Go to", ["Dashboard", "Portfolio"], index=0)
target = "dashboard" if nav == "Dashboard" else "portfolio"
if st.session_state.page != target and st.sidebar.button("Open", use_container_width=True):
    st.session_state.page = target
    st.session_state.selected_key = None
    st.rerun()

st.sidebar.markdown("### Data Source")
uploaded = st.sidebar.file_uploader("Upload Excel (Project Details bi-weekly update)", type=["xlsx"])
use_sample = st.sidebar.checkbox("Use local file name if exists", value=True)

df = None
error = None
try:
    if uploaded is not None:
        df = load_excel_smart(uploaded)
    else:
        if use_sample:
            local_path = "Project Details bi-weekly update 2026.xlsx"
            df = load_excel_smart(local_path)
except Exception as e:
    error = str(e)
    df = None

if df is None:
    st.warning("请在左侧上传 Excel，或将 Project Details bi-weekly update 2026.xlsx 放在 app.py 同目录。")
    if error:
        st.code(error)
    st.stop()

missing_cols = [c for c in [COL_TYPE, COL_NAME] if c not in df.columns]
if missing_cols:
    st.error(f"Excel 缺少必要列：{missing_cols}。请确认使用 Project Details bi-weekly update 2026.xlsx 的 Weekly project update 表。")
    st.stop()

df_view = merged_view(df)

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
            st.caption("（建议演示：先Open Detail展示PPT布局，再Update演示周会更新。）")

    st.divider()
    st.markdown("## ⬇ Export (Demo)")
    st.caption("Demo阶段：更新保存在会话中；可导出带更新字段的Excel供会后讨论。")

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
        st.error("找不到选中的条目（可能筛选变化导致）。")
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()

    render_detail_ppt_layout(row_df.iloc[0], key)

elif st.session_state.page == "update":
    if not st.session_state.selected_key:
        st.session_state.page = "portfolio"
        st.rerun()

    key = st.session_state.selected_key
    row_df = df_view[df_view["_key"] == key]
    if row_df.empty:
        st.error("找不到选中的条目（可能筛选变化导致）。")
        st.session_state.page = "portfolio"
        st.session_state.selected_key = None
        st.rerun()

    render_update_form(row_df.iloc[0], key)

else:
    st.session_state.page = "dashboard"
    st.rerun()