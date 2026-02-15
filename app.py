import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

TESTRAIL_URL = st.secrets.get("TESTRAIL_URL", "")
TESTRAIL_USER = st.secrets.get("TESTRAIL_USER", "")
TESTRAIL_API_KEY = st.secrets.get("TESTRAIL_API_KEY", "")

# ─────────────────────────────────────────────────────────────
# BU → Plan mapping — add new BUs here
# ─────────────────────────────────────────────────────────────
BU_PLANS = {
    "Watsons Turkey": {"plan_id": 61979},
    # "Another BU": {"plan_id": 12345},
}

REVIEW_NOTES_FIELD = "custom_review_note"


# ─────────────────────────────────────────────────────────────
# TestRail API client
# ─────────────────────────────────────────────────────────────
class TestRailClient:
    def __init__(self, base_url: str, user: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, api_key)
        self.session.headers.update({"Content-Type": "application/json"})

    def _get(self, endpoint: str, params: dict | None = None):
        url = f"{self.base_url}/index.php?/api/v2/{endpoint}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}&{query}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_plan(self, plan_id: int) -> dict:
        return self._get(f"get_plan/{plan_id}")

    def get_tests(self, run_id: int) -> list[dict]:
        tests = []
        offset = 0
        limit = 250
        while True:
            data = self._get(f"get_tests/{run_id}", {"limit": limit, "offset": offset})
            if isinstance(data, dict) and "tests" in data:
                tests.extend(data["tests"])
                if data.get("_links", {}).get("next") is None:
                    break
                offset += limit
            elif isinstance(data, list):
                tests.extend(data)
                break
            else:
                break
        return tests

    def get_statuses(self) -> list[dict]:
        return self._get("get_statuses")

    def get_priorities(self) -> list[dict]:
        return self._get("get_priorities")

    def get_case_types(self) -> list[dict]:
        return self._get("get_case_types")


@st.cache_data(ttl=300)
def fetch_plan_data(plan_id: int):
    client = TestRailClient(TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY)
    plan = client.get_plan(plan_id)
    statuses_raw = client.get_statuses()
    priorities_raw = client.get_priorities()
    case_types_raw = client.get_case_types()

    status_map = {s["id"]: s["label"] for s in statuses_raw}
    priority_map = {p["id"]: p["name"] for p in priorities_raw}
    type_map = {t["id"]: t["name"] for t in case_types_raw}

    runs_info = []
    all_tests = []
    for entry in plan.get("entries", []):
        for run in entry.get("runs", []):
            run_id = run["id"]
            run_name = run["name"]
            runs_info.append({"run_id": run_id, "run_name": run_name, "run_url": run.get("url", "")})
            tests = client.get_tests(run_id)
            for t in tests:
                t["_run_name"] = run_name
                t["_run_id"] = run_id
            all_tests.extend(tests)

    return plan, runs_info, all_tests, status_map, priority_map, type_map


# ─────────────────────────────────────────────────────────────
# Status configuration
# ─────────────────────────────────────────────────────────────
def build_status_group_map(status_map: dict[int, str]) -> dict[str, str]:
    explicit = {
        "Passed": "Passed",
        "Passed with Issue": "Passed with Issue",
        "Passed with Stub": "Passed with Stub",
        "To Do": "To Do",
        "To-do": "To Do",
        "Blocked": "Blocked",
        "Failed (Medium)": "Failed (Medium)",
        "Not Applicable": "Not Applicable",
        "Failed": "Failed",
        "Retest": "To Do",
        "Untested": "Untested",
    }
    mapping = {}
    for label in status_map.values():
        mapping[label] = explicit.get(label, label)
    return mapping


STATUS_COLORS = {
    "Passed": "#10b981",
    "Passed with Issue": "#34d399",
    "Passed with Stub": "#a3e635",
    "To Do": "#6366f1",
    "Blocked": "#f97316",
    "Failed": "#ef4444",
    "Failed (Medium)": "#f59e0b",
    "Not Applicable": "#94a3b8",
    "Untested": "#cbd5e1",
}

STATUS_DESCRIPTIONS = {
    "Passed": "Merged into the master branch.",
    "Passed with Issue": "Pull Request (PR) raised — review in progress.",
    "Passed with Stub": "Implementation completed — PR yet to be raised.",
    "To Do": "Picked by team.",
    "Blocked": "Blocked due to an issue, pending test data, or other dependencies.",
    "Failed": "Test execution failed.",
    "Failed (Medium)": "Automation not feasible on UAT; will be revisited later.",
    "Not Applicable": "Excluded from automation (device, manual, wait time, config or precondition conflicts).",
    "Untested": "Not yet executed.",
}

BASE_STATUS_ORDER = [
    "Passed", "Passed with Issue", "Passed with Stub",
    "To Do", "Blocked", "Failed", "Failed (Medium)",
    "Untested", "Not Applicable",
]


def build_testrail_test_url(test: dict) -> str:
    case_id = test.get("case_id")
    if case_id:
        return f"{TESTRAIL_URL}/index.php?/cases/view/{case_id}"
    return ""


# ─────────────────────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────────────────────
def inject_css():
    st.markdown("""
    <style>
        /* Clean header area */
        .block-container { padding-top: 2rem; }

        /* KPI cards */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 12px;
            margin: 1rem 0 1.5rem 0;
        }
        .kpi-card {
            border-radius: 10px;
            padding: 16px 14px;
            text-align: center;
            border: 1px solid rgba(0,0,0,0.06);
        }
        .kpi-card .label {
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: rgba(255,255,255,0.85);
            margin-bottom: 4px;
        }
        .kpi-card .value {
            font-size: 1.8rem;
            font-weight: 700;
            color: #fff;
            line-height: 1.1;
        }
        .kpi-card .pct {
            font-size: 0.75rem;
            color: rgba(255,255,255,0.7);
            margin-top: 2px;
        }

        /* Progress section */
        .progress-wrapper {
            background: #f8fafc;
            border-radius: 10px;
            padding: 20px 24px;
            margin: 1rem 0;
            border: 1px solid #e2e8f0;
        }
        .progress-bar-bg {
            width: 100%;
            height: 24px;
            background: #e2e8f0;
            border-radius: 12px;
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            border-radius: 12px;
            transition: width 0.6s ease;
        }
        .progress-label {
            margin-top: 8px;
            font-size: 0.85rem;
            color: #475569;
        }

        /* Section titles */
        .section-title {
            font-size: 1.1rem;
            font-weight: 600;
            color: #1e293b;
            margin: 2rem 0 0.8rem 0;
            padding-bottom: 6px;
            border-bottom: 2px solid #e2e8f0;
        }

        /* Expander badges */
        .status-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            color: #fff;
            margin-right: 8px;
            vertical-align: middle;
        }

        /* Plan header */
        .plan-header {
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
            color: white;
            padding: 24px 28px;
            border-radius: 12px;
            margin-bottom: 1.5rem;
        }
        .plan-header h2 {
            margin: 0 0 6px 0;
            font-size: 1.3rem;
            font-weight: 600;
        }
        .plan-header .meta {
            font-size: 0.85rem;
            color: #94a3b8;
        }
        .plan-header a {
            color: #60a5fa;
            text-decoration: none;
        }
        .plan-header a:hover {
            text-decoration: underline;
        }

        /* Run pills */
        .run-pill {
            display: inline-block;
            background: rgba(255,255,255,0.1);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            color: #e2e8f0;
            margin: 6px 6px 0 0;
        }

        /* Total badge */
        .total-badge {
            display: inline-block;
            background: #f1f5f9;
            padding: 6px 16px;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 600;
            color: #334155;
            margin-top: 4px;
        }

        /* Legend */
        .legend-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 8px;
            margin-top: 8px;
        }
        .legend-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 8px 12px;
            border-radius: 8px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
        }
        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-top: 5px;
            flex-shrink: 0;
        }
        .legend-text strong {
            font-size: 0.82rem;
            color: #1e293b;
        }
        .legend-text span {
            font-size: 0.75rem;
            color: #64748b;
        }

        /* Hide Streamlit default elements for cleaner look */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Component helpers
# ─────────────────────────────────────────────────────────────
def render_plan_header(plan: dict, plan_id: int, runs_info: list):
    plan_url = plan.get("url", f"{TESTRAIL_URL}/index.php?/plans/view/{plan_id}")
    runs_html = "".join([f'<span class="run-pill">{r["run_name"]}</span>' for r in runs_info])
    st.markdown(f"""
    <div class="plan-header">
        <h2>{plan.get("name", "N/A")}</h2>
        <div class="meta">
            <a href="{plan_url}" target="_blank">Open in TestRail &rarr;</a>
        </div>
        <div style="margin-top: 10px;">{runs_html}</div>
    </div>
    """, unsafe_allow_html=True)


def render_kpi_cards(status_order: list, status_counts, total_tests: int):
    cards_html = ""
    for status in status_order:
        count = status_counts.get(status, 0)
        pct = (count / total_tests * 100) if total_tests > 0 else 0
        color = STATUS_COLORS.get(status, "#64748b")
        cards_html += f"""
        <div class="kpi-card" style="background: {color};">
            <div class="label">{status}</div>
            <div class="value">{count}</div>
            <div class="pct">{pct:.1f}%</div>
        </div>
        """
    st.markdown(f'<div class="kpi-grid">{cards_html}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="total-badge">Total: {total_tests} tests</div>', unsafe_allow_html=True)


def render_progress(status_counts, total_tests: int):
    done_statuses = {"Passed", "Passed with Issue", "Passed with Stub"}
    done_count = sum(status_counts.get(s, 0) for s in done_statuses)
    na_count = status_counts.get("Not Applicable", 0)
    actionable = total_tests - na_count
    pct = (done_count / actionable * 100) if actionable > 0 else 0

    # gradient green
    bar_color = "linear-gradient(90deg, #10b981 0%, #34d399 100%)"
    st.markdown(f"""
    <div class="progress-wrapper">
        <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
            <span style="font-weight:600; color:#1e293b;">Automation Progress</span>
            <span style="font-weight:700; color:#10b981; font-size:1.1rem;">{pct:.1f}%</span>
        </div>
        <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width:{pct}%; background:{bar_color};"></div>
        </div>
        <div class="progress-label">
            <strong>{done_count}</strong> of <strong>{actionable}</strong> actionable tests completed
            &nbsp;&middot;&nbsp; {na_count} not applicable excluded
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_legend(status_order: list):
    items = ""
    for s in status_order:
        color = STATUS_COLORS.get(s, "#64748b")
        desc = STATUS_DESCRIPTIONS.get(s, "")
        items += f"""
        <div class="legend-item">
            <div class="legend-dot" style="background:{color};"></div>
            <div class="legend-text"><strong>{s}</strong><br><span>{desc}</span></div>
        </div>
        """
    st.markdown(f'<div class="legend-grid">{items}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Main app
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Automation Backlog Dashboard",
        page_icon="https://cdn-icons-png.flaticon.com/512/2282/2282188.png",
        layout="wide",
    )
    inject_css()

    # --- Sidebar ---
    with st.sidebar:
        st.markdown("#### Settings")
        bu_name = st.selectbox("Business Unit", list(BU_PLANS.keys()))
        st.markdown("---")

    if not all([TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY]):
        st.error("TestRail credentials not configured. Set them in Streamlit Secrets.")
        st.code(
            'TESTRAIL_URL = "https://elabaswatson.testrail.io"\n'
            'TESTRAIL_USER = "your-email@example.com"\n'
            'TESTRAIL_API_KEY = "your-api-key"',
            language="toml",
        )
        return

    plan_cfg = BU_PLANS[bu_name]
    plan_id = plan_cfg["plan_id"]

    with st.spinner("Loading data from TestRail..."):
        try:
            plan, runs_info, all_tests, status_map, priority_map, type_map = fetch_plan_data(plan_id)
        except requests.HTTPError as e:
            st.error(f"TestRail API error: {e}")
            return
        except Exception as e:
            st.error(f"Error: {e}")
            return

    # --- DataFrame ---
    status_group_map = build_status_group_map(status_map)
    rows = []
    for t in all_tests:
        status_label = status_map.get(t.get("status_id"), "Unknown")
        group = status_group_map.get(status_label, status_label)
        rows.append({
            "Test ID": t.get("id"),
            "Case ID": t.get("case_id"),
            "Title": t.get("title", ""),
            "Status (TestRail)": status_label,
            "Status": group,
            "Priority": priority_map.get(t.get("priority_id"), "Unknown"),
            "Type": type_map.get(t.get("type_id"), "Unknown"),
            "Run": t.get("_run_name", ""),
            "Review Notes": t.get(REVIEW_NOTES_FIELD, "") or "",
            "TestRail Link": build_testrail_test_url(t),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        st.warning("No tests found in this plan.")
        return

    # --- Plan header ---
    render_plan_header(plan, plan_id, runs_info)

    # --- Run filter ---
    run_names = ["All Runs"] + sorted(df["Run"].unique().tolist())
    selected_run = st.sidebar.selectbox("Filter by Run", run_names)
    df_filtered = df[df["Run"] == selected_run].copy() if selected_run != "All Runs" else df.copy()
    total_tests = len(df_filtered)

    # --- Status order ---
    all_statuses_in_data = df_filtered["Status"].unique().tolist()
    status_order = [s for s in BASE_STATUS_ORDER if s in all_statuses_in_data]
    for s in all_statuses_in_data:
        if s not in status_order:
            status_order.append(s)

    # --- KPI ---
    status_counts = df_filtered["Status"].value_counts()
    render_kpi_cards(status_order, status_counts, total_tests)

    # --- Progress ---
    render_progress(status_counts, total_tests)

    # --- Charts ---
    st.markdown('<div class="section-title">Distribution</div>', unsafe_allow_html=True)
    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        chart_data = [{"Status": s, "Count": status_counts.get(s, 0)}
                      for s in status_order if status_counts.get(s, 0) > 0]
        if chart_data:
            fig = px.pie(
                pd.DataFrame(chart_data),
                names="Status", values="Count", color="Status",
                color_discrete_map=STATUS_COLORS, hole=0.45,
            )
            fig.update_traces(
                textposition="inside", textinfo="value+percent",
                textfont_size=12,
            )
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5),
                font=dict(family="Inter, sans-serif"),
                height=360,
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if len(runs_info) > 1:
            run_status = df.groupby(["Run", "Status"]).size().reset_index(name="Count")
            fig2 = px.bar(
                run_status, x="Run", y="Count", color="Status",
                color_discrete_map=STATUS_COLORS, barmode="stack",
                category_orders={"Status": status_order},
            )
            fig2.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                font=dict(family="Inter, sans-serif"),
                xaxis_title="", yaxis_title="Tests",
                height=360,
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Single run in this plan.")

    # --- Detail tables ---
    st.markdown('<div class="section-title">Detail by Status</div>', unsafe_allow_html=True)

    for status in status_order:
        group_df = df_filtered[df_filtered["Status"] == status]
        count = len(group_df)
        if count == 0:
            continue

        color = STATUS_COLORS.get(status, "#64748b")
        desc = STATUS_DESCRIPTIONS.get(status, "")

        with st.expander(f"{status}  —  {count} tests"):
            # description pill
            st.caption(desc)

            display_cols = ["Case ID", "Title", "Priority", "Type", "Run"]
            if status == "Not Applicable":
                display_cols.append("Review Notes")

            display_df = group_df[display_cols + ["TestRail Link"]].copy()
            display_df = display_df.rename(columns={"TestRail Link": "Link"})

            st.dataframe(
                display_df,
                column_config={
                    "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                    "Case ID": st.column_config.NumberColumn("Case ID", format="%d"),
                },
                hide_index=True,
                use_container_width=True,
            )

    # --- Legend ---
    st.markdown('<div class="section-title">Status Legend</div>', unsafe_allow_html=True)
    render_legend(status_order)


if __name__ == "__main__":
    main()
