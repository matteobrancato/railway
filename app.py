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

STATUS_EMOJI = {
    "Passed": ":green[Passed]",
    "Passed with Issue": ":green[Passed with Issue]",
    "Passed with Stub": ":green[Passed with Stub]",
    "To Do": ":blue[To Do]",
    "Blocked": ":orange[Blocked]",
    "Failed": ":red[Failed]",
    "Failed (Medium)": ":orange[Failed (Medium)]",
    "Not Applicable": "Not Applicable",
    "Untested": "Untested",
}

STATUS_DESCRIPTIONS = {
    "Passed": "Merged into the master branch.",
    "Passed with Issue": "PR raised — review in progress.",
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
# Main app
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Automation Backlog Dashboard", layout="wide")

    # --- Sidebar ---
    with st.sidebar:
        st.title("Settings")
        bu_name = st.selectbox("Business Unit", list(BU_PLANS.keys()))

    if not all([TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY]):
        st.error(
            "TestRail credentials not configured. "
            "Set `TESTRAIL_URL`, `TESTRAIL_USER`, and `TESTRAIL_API_KEY` in Streamlit Secrets."
        )
        return

    plan_id = BU_PLANS[bu_name]["plan_id"]

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

    # ── Plan header ──
    st.title("Automation Backlog Dashboard")
    plan_url = plan.get("url", f"{TESTRAIL_URL}/index.php?/plans/view/{plan_id}")
    st.markdown(f"**{plan.get('name', 'N/A')}** &mdash; [Open in TestRail]({plan_url})")

    run_names_display = " / ".join([f"`{r['run_name']}`" for r in runs_info])
    st.markdown(f"Runs: {run_names_display}")

    # --- Run filter in sidebar ---
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

    status_counts = df_filtered["Status"].value_counts()

    st.divider()

    # ── KPI metrics ──
    # Split into two rows if many statuses
    row_size = min(len(status_order), 5)
    row1_statuses = status_order[:row_size]
    row2_statuses = status_order[row_size:]

    cols = st.columns(len(row1_statuses))
    for i, status in enumerate(row1_statuses):
        count = status_counts.get(status, 0)
        pct = (count / total_tests * 100) if total_tests > 0 else 0
        with cols[i]:
            colored = STATUS_EMOJI.get(status, status)
            st.metric(label=status, value=count, delta=f"{pct:.1f}%")

    if row2_statuses:
        cols2 = st.columns(len(row2_statuses))
        for i, status in enumerate(row2_statuses):
            count = status_counts.get(status, 0)
            pct = (count / total_tests * 100) if total_tests > 0 else 0
            with cols2[i]:
                st.metric(label=status, value=count, delta=f"{pct:.1f}%")

    st.markdown(f"**Total: {total_tests} tests**")

    # ── Automation Progress ──
    st.divider()
    done_statuses = {"Passed", "Passed with Issue", "Passed with Stub"}
    done_count = sum(status_counts.get(s, 0) for s in done_statuses)
    na_count = status_counts.get("Not Applicable", 0)
    actionable = total_tests - na_count
    pct = (done_count / actionable) if actionable > 0 else 0

    prog_col1, prog_col2 = st.columns([3, 1])
    with prog_col1:
        st.markdown("**Automation Progress**")
        st.progress(pct)
    with prog_col2:
        st.metric("Completed", f"{pct*100:.1f}%", delta=f"{done_count}/{actionable}")

    st.caption(f"{done_count} of {actionable} actionable tests completed — {na_count} not applicable excluded")

    # ── Charts ──
    st.divider()
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("Status Distribution")
        chart_data = [{"Status": s, "Count": status_counts.get(s, 0)}
                      for s in status_order if status_counts.get(s, 0) > 0]
        if chart_data:
            fig = go.Figure(data=[go.Pie(
                labels=[d["Status"] for d in chart_data],
                values=[d["Count"] for d in chart_data],
                marker=dict(colors=[STATUS_COLORS.get(d["Status"], "#64748b") for d in chart_data]),
                hole=0.45,
                textposition="inside",
                textinfo="value+percent",
                textfont_size=12,
            )])
            fig.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                height=380,
                showlegend=True,
                legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="center", x=0.5),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_chart2:
        st.subheader("Status by Run")
        if len(runs_info) > 1:
            run_status = df.groupby(["Run", "Status"]).size().reset_index(name="Count")
            fig2 = px.bar(
                run_status, x="Run", y="Count", color="Status",
                color_discrete_map=STATUS_COLORS, barmode="stack",
                category_orders={"Status": status_order},
            )
            fig2.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                height=380,
                legend=dict(orientation="h", yanchor="top", y=-0.1, xanchor="center", x=0.5),
                xaxis_title="", yaxis_title="Tests",
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Single run in this plan.")

    # ── Detail tables ──
    st.divider()
    st.subheader("Detail by Status")

    for status in status_order:
        group_df = df_filtered[df_filtered["Status"] == status]
        count = len(group_df)
        if count == 0:
            continue

        desc = STATUS_DESCRIPTIONS.get(status, "")
        expander_label = f"{status} — {count} tests"

        with st.expander(expander_label):
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

    # ── Legend ──
    st.divider()
    st.subheader("Status Legend")
    for status in status_order:
        desc = STATUS_DESCRIPTIONS.get(status, "")
        colored = STATUS_EMOJI.get(status, status)
        st.markdown(f"- **{colored}**: {desc}")


if __name__ == "__main__":
    main()
