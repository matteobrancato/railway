import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

# TestRail connection — set via Streamlit secrets or environment
# In .streamlit/secrets.toml:
#   TESTRAIL_URL = "https://elabaswatson.testrail.io"
#   TESTRAIL_USER = "your-email@example.com"
#   TESTRAIL_API_KEY = "your-api-key"

TESTRAIL_URL = st.secrets.get("TESTRAIL_URL", "")
TESTRAIL_USER = st.secrets.get("TESTRAIL_USER", "")
TESTRAIL_API_KEY = st.secrets.get("TESTRAIL_API_KEY", "")

# ─────────────────────────────────────────────────────────────
# BU → Plan mapping
# Add new BUs here. The plan_id is the numeric ID from the TestRail URL.
# ─────────────────────────────────────────────────────────────
BU_PLANS = {
    "Watsons Turkey": {"plan_id": 61979},
    # "Another BU": {"plan_id": 12345},
}

# ─────────────────────────────────────────────────────────────
# Custom field for "Review Notes" on test objects.
# Check your TestRail Admin > Customizations to find the exact
# system name. It will appear as custom_<system_name> in the API.
# ─────────────────────────────────────────────────────────────
REVIEW_NOTES_FIELD = "custom_review_note"  # adjust if different


# ─────────────────────────────────────────────────────────────
# TestRail API helpers
# ─────────────────────────────────────────────────────────────
class TestRailClient:
    def __init__(self, base_url: str, user: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, api_key)
        self.session.headers.update({"Content-Type": "application/json"})

    def _get(self, endpoint: str, params: dict | None = None):
        # TestRail uses ?/api/v2/ in URLs, so extra query params must be
        # appended with & rather than passed as separate params (which would
        # add a second '?' and break the request).
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
        """Fetch all tests for a run, handling pagination."""
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
    """Fetch plan, tests for every run, and reference data."""
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
# Status label mapping (TestRail status label → our semantic group)
# Adjust the left side to match your TestRail custom status labels.
# ─────────────────────────────────────────────────────────────
def build_status_group_map(status_map: dict[int, str]) -> dict[str, str]:
    """Dynamically map every TestRail status label to our semantic groups.

    Known labels are mapped explicitly. Anything unknown is kept as-is
    so it still shows up in the dashboard (in an 'Other' bucket).
    """
    explicit = {
        "Passed": "Passed",
        "Passed with Issue": "Passed with Issue",
        "Passed with Stub": "Passed with Stub",
        "To Do": "To Do",
        "To-do": "To Do",
        "Blocked": "Blocked",
        "Failed (Medium)": "Failed (Medium)",
        "Not Applicable": "Not Applicable",
        # system statuses
        "Failed": "Failed",
        "Retest": "To Do",
        "Untested": "Untested",
    }
    mapping = {}
    for label in status_map.values():
        mapping[label] = explicit.get(label, label)
    return mapping

STATUS_COLORS = {
    "Passed": "#28a745",
    "Passed with Issue": "#80c565",
    "Passed with Stub": "#d4c84e",
    "To Do": "#3498db",
    "Blocked": "#e67e22",
    "Failed": "#e74c3c",
    "Failed (Medium)": "#f39c12",
    "Not Applicable": "#95a5a6",
    "Untested": "#999999",
}

STATUS_DESCRIPTIONS = {
    "Passed": "Merged into the master branch.",
    "Passed with Issue": "Pull Request (PR) raised — review in progress.",
    "Passed with Stub": "Implementation completed — PR yet to be raised.",
    "To Do": "Picked by team.",
    "Blocked": "Blocked due to an issue, pending test data, or other dependencies.",
    "Failed": "Test execution failed.",
    "Failed (Medium)": "Automation not feasible on UAT; will be revisited later for development-only automation.",
    "Not Applicable": "Excluded from automation (not relevant for device, requires manual intervention, excessive wait, config conflicts, or precondition conflicts).",
    "Untested": "Not yet executed.",
}

# Ordered for display — built dynamically to catch any extra statuses
BASE_STATUS_ORDER = [
    "Passed",
    "Passed with Issue",
    "Passed with Stub",
    "To Do",
    "Blocked",
    "Failed",
    "Failed (Medium)",
    "Untested",
    "Not Applicable",
]


def build_testrail_test_url(test: dict) -> str:
    """Build a direct link to the test case in TestRail."""
    case_id = test.get("case_id")
    if case_id:
        return f"{TESTRAIL_URL}/index.php?/cases/view/{case_id}"
    return ""


# ─────────────────────────────────────────────────────────────
# Streamlit App
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Automation Backlog Dashboard", layout="wide")
    st.title("Automation Backlog Dashboard")

    # --- Sidebar ---
    st.sidebar.header("Configuration")
    bu_name = st.sidebar.selectbox("Select Business Unit", list(BU_PLANS.keys()))

    if not all([TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY]):
        st.error(
            "TestRail credentials are not configured. "
            "Please set `TESTRAIL_URL`, `TESTRAIL_USER`, and `TESTRAIL_API_KEY` "
            "in `.streamlit/secrets.toml`."
        )
        st.code(
            '[secrets]\n'
            'TESTRAIL_URL = "https://elabaswatson.testrail.io"\n'
            'TESTRAIL_USER = "your-email@example.com"\n'
            'TESTRAIL_API_KEY = "your-api-key"',
            language="toml",
        )
        return

    plan_cfg = BU_PLANS[bu_name]
    plan_id = plan_cfg["plan_id"]

    with st.spinner("Fetching data from TestRail..."):
        try:
            plan, runs_info, all_tests, status_map, priority_map, type_map = fetch_plan_data(plan_id)
        except requests.HTTPError as e:
            st.error(f"TestRail API error: {e}")
            return
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            return

    # --- Build DataFrame ---
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

    # --- Plan Header ---
    st.subheader(f"Plan: {plan.get('name', 'N/A')}")
    plan_url = plan.get("url", f"{TESTRAIL_URL}/index.php?/plans/view/{plan_id}")
    st.markdown(f"[Open plan in TestRail]({plan_url})")

    runs_text = " | ".join([f"**{r['run_name']}**" for r in runs_info])
    st.markdown(f"Runs: {runs_text}")

    st.divider()

    # --- Run filter ---
    run_names = ["All Runs"] + sorted(df["Run"].unique().tolist())
    selected_run = st.sidebar.selectbox("Filter by Run", run_names)
    if selected_run != "All Runs":
        df_filtered = df[df["Run"] == selected_run].copy()
    else:
        df_filtered = df.copy()

    total_tests = len(df_filtered)

    # Build display order: base order + any extra statuses found in data
    all_statuses_in_data = df_filtered["Status"].unique().tolist()
    status_order = [s for s in BASE_STATUS_ORDER if s in all_statuses_in_data]
    for s in all_statuses_in_data:
        if s not in status_order:
            status_order.append(s)

    # --- KPI row ---
    st.markdown("### Overview")
    status_counts = df_filtered["Status"].value_counts()

    cols = st.columns(len(status_order))
    for i, status in enumerate(status_order):
        count = status_counts.get(status, 0)
        pct = (count / total_tests * 100) if total_tests > 0 else 0
        with cols[i]:
            st.metric(
                label=status,
                value=count,
                delta=f"{pct:.1f}%",
            )

    st.markdown(f"**Total tests: {total_tests}**")

    st.divider()

    # --- Charts ---
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.markdown("### Status Distribution")
        chart_data = []
        for s in status_order:
            c = status_counts.get(s, 0)
            if c > 0:
                chart_data.append({"Status": s, "Count": c})
        if chart_data:
            fig_pie = px.pie(
                pd.DataFrame(chart_data),
                names="Status",
                values="Count",
                color="Status",
                color_discrete_map=STATUS_COLORS,
                hole=0.4,
            )
            fig_pie.update_traces(textposition="inside", textinfo="value+percent")
            fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig_pie, use_container_width=True)

    with col_chart2:
        st.markdown("### Status by Run")
        if len(runs_info) > 1:
            run_status = df.groupby(["Run", "Status"]).size().reset_index(name="Count")
            fig_bar = px.bar(
                run_status,
                x="Run",
                y="Count",
                color="Status",
                color_discrete_map=STATUS_COLORS,
                barmode="stack",
                category_orders={"Status": status_order},
            )
            fig_bar.update_layout(margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("Only one run in this plan — bar chart skipped.")

    # --- Progress bar ---
    st.markdown("### Automation Progress")
    done_statuses = {"Passed", "Passed with Issue", "Passed with Stub"}
    done_count = sum(status_counts.get(s, 0) for s in done_statuses)
    not_applicable_count = status_counts.get("Not Applicable", 0)
    actionable_total = total_tests - not_applicable_count
    progress = (done_count / actionable_total) if actionable_total > 0 else 0

    st.progress(progress)
    st.markdown(
        f"**{done_count}** out of **{actionable_total}** actionable tests completed "
        f"(**{progress*100:.1f}%**) — {not_applicable_count} not applicable excluded"
    )

    st.divider()

    # --- Detail section per status group ---
    st.markdown("### Detail by Status")

    for status in status_order:
        group_df = df_filtered[df_filtered["Status"] == status]
        count = len(group_df)
        if count == 0:
            continue

        color = STATUS_COLORS.get(status, "#666")
        with st.expander(f":{color[1:]}[●] **{status}** — {count} tests | {STATUS_DESCRIPTIONS.get(status, '')}"):
            display_cols = ["Case ID", "Title", "Priority", "Type", "Run"]

            # For Not Applicable, show Review Notes
            if status == "Not Applicable":
                display_cols.append("Review Notes")

            display_df = group_df[display_cols + ["TestRail Link"]].copy()
            display_df = display_df.rename(columns={"TestRail Link": "Link"})

            st.dataframe(
                display_df,
                column_config={
                    "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                },
                hide_index=True,
                use_container_width=True,
            )

    # --- Status Legend ---
    st.divider()
    st.markdown("### Status Legend")
    for status in status_order:
        color = STATUS_COLORS.get(status, "#666")
        st.markdown(f"- **{status}**: {STATUS_DESCRIPTIONS.get(status, '')}")


if __name__ == "__main__":
    main()
