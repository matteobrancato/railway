import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

TESTRAIL_URL = st.secrets.get("TESTRAIL_URL", "")
TESTRAIL_USER = st.secrets.get("TESTRAIL_USER", "")
TESTRAIL_API_KEY = st.secrets.get("TESTRAIL_API_KEY", "")

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
# Status config
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
    return {label: explicit.get(label, label) for label in status_map.values()}


STATUS_COLORS = {
    "Passed": "#059669",
    "Passed with Issue": "#10b981",
    "Passed with Stub": "#84cc16",
    "To Do": "#6366f1",
    "Blocked": "#ea580c",
    "Failed": "#dc2626",
    "Failed (Medium)": "#d97706",
    "Not Applicable": "#64748b",
    "Untested": "#94a3b8",
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
    return f"{TESTRAIL_URL}/index.php?/cases/view/{case_id}" if case_id else ""


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Automation Backlog", layout="wide")

    # ── Global styles ──
    st.markdown("""<style>
    .block-container {padding-top:1.5rem; padding-bottom:1rem;}
    [data-testid="stMetricValue"] {font-size:1.6rem;}
    [data-testid="stMetricDelta"] {font-size:0.8rem;}
    div[data-testid="stExpander"] details summary span {font-size:0.95rem;}
    #MainMenu, footer, header {visibility:hidden;}
    </style>""", unsafe_allow_html=True)

    # ── Sidebar ──
    with st.sidebar:
        st.markdown("### Filters")
        bu_name = st.selectbox("Business Unit", list(BU_PLANS.keys()))

    if not all([TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY]):
        st.error("TestRail credentials not configured. Set them in Streamlit Secrets.")
        return

    plan_id = BU_PLANS[bu_name]["plan_id"]

    with st.spinner("Loading from TestRail..."):
        try:
            plan, runs_info, all_tests, status_map, priority_map, type_map = fetch_plan_data(plan_id)
        except Exception as e:
            st.error(f"Error: {e}")
            return

    # ── Build dataframe ──
    sgm = build_status_group_map(status_map)
    rows = []
    for t in all_tests:
        sl = status_map.get(t.get("status_id"), "Unknown")
        rows.append({
            "Case ID": t.get("case_id"),
            "Title": t.get("title", ""),
            "Status": sgm.get(sl, sl),
            "Priority": priority_map.get(t.get("priority_id"), "—"),
            "Type": type_map.get(t.get("type_id"), "—"),
            "Run": t.get("_run_name", ""),
            "Review Notes": t.get(REVIEW_NOTES_FIELD, "") or "",
            "Link": build_testrail_test_url(t),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        st.warning("No tests found.")
        return

    # ── Header ──
    plan_url = plan.get("url", f"{TESTRAIL_URL}/index.php?/plans/view/{plan_id}")
    st.markdown(f"## Automation Backlog — {bu_name}")
    st.caption(f"{plan.get('name', '')}  ·  [Open in TestRail]({plan_url})")

    # ── Run filter ──
    run_names = ["All Runs"] + sorted(df["Run"].unique().tolist())
    selected_run = st.sidebar.selectbox("Run", run_names)
    dff = df[df["Run"] == selected_run].copy() if selected_run != "All Runs" else df.copy()
    total = len(dff)

    # ── Status order ──
    present = dff["Status"].unique().tolist()
    order = [s for s in BASE_STATUS_ORDER if s in present]
    for s in present:
        if s not in order:
            order.append(s)
    counts = dff["Status"].value_counts()

    # ── KPI Cards (Plotly indicator) ──
    st.markdown("")
    fig_kpi = go.Figure()
    n = len(order)
    for i, status in enumerate(order):
        c = int(counts.get(status, 0))
        pct = c / total * 100 if total else 0
        color = STATUS_COLORS.get(status, "#64748b")
        fig_kpi.add_trace(go.Indicator(
            mode="number",
            value=c,
            number=dict(font=dict(size=36, color=color)),
            title=dict(text=f"<b>{status}</b><br><span style='font-size:0.75em;color:#888'>{pct:.1f}%</span>",
                       font=dict(size=13)),
            domain=dict(
                x=[i / n + 0.005, (i + 1) / n - 0.005],
                y=[0, 1],
            ),
        ))
    fig_kpi.update_layout(
        height=120,
        margin=dict(t=30, b=0, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_kpi, use_container_width=True, config={"displayModeBar": False})

    st.caption(f"**{total}** tests total across **{len(runs_info)}** run(s)")

    # ── Progress ──
    done_set = {"Passed", "Passed with Issue", "Passed with Stub"}
    done = sum(int(counts.get(s, 0)) for s in done_set)
    na = int(counts.get("Not Applicable", 0))
    actionable = total - na
    pct_done = done / actionable if actionable else 0

    # Plotly horizontal bar for progress
    fig_prog = go.Figure()
    fig_prog.add_trace(go.Bar(
        x=[pct_done * 100], y=[""], orientation="h",
        marker=dict(color="#059669", cornerradius=6),
        text=f"  {pct_done*100:.1f}%", textposition="inside",
        textfont=dict(color="white", size=14),
        hoverinfo="skip",
    ))
    fig_prog.add_trace(go.Bar(
        x=[(1 - pct_done) * 100], y=[""], orientation="h",
        marker=dict(color="#e2e8f0", cornerradius=6),
        hoverinfo="skip", showlegend=False,
    ))
    fig_prog.update_layout(
        barmode="stack",
        height=56,
        margin=dict(t=0, b=0, l=0, r=0),
        xaxis=dict(visible=False, range=[0, 100]),
        yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig_prog, use_container_width=True, config={"displayModeBar": False})
    st.caption(
        f"**{done}** / **{actionable}** actionable tests automated · "
        f"{na} not applicable excluded"
    )

    st.markdown("")

    # ── Charts ──
    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.markdown("##### Status Distribution")
        cd = [{"Status": s, "Count": int(counts.get(s, 0))} for s in order if counts.get(s, 0)]
        fig_pie = go.Figure(go.Pie(
            labels=[d["Status"] for d in cd],
            values=[d["Count"] for d in cd],
            marker=dict(colors=[STATUS_COLORS.get(d["Status"], "#64748b") for d in cd]),
            hole=0.5, textposition="inside", textinfo="value+percent",
            textfont_size=11, sort=False,
        ))
        fig_pie.update_layout(
            height=360, margin=dict(t=10, b=10, l=10, r=10),
            legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center", font=dict(size=11)),
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_pie, use_container_width=True, config={"displayModeBar": False})

    with c2:
        st.markdown("##### Desktop vs Mobile")
        if len(runs_info) > 1:
            rs = df.groupby(["Run", "Status"]).size().reset_index(name="Count")
            fig_bar = go.Figure()
            for status in order:
                sub = rs[rs["Status"] == status]
                if not sub.empty:
                    fig_bar.add_trace(go.Bar(
                        x=sub["Run"], y=sub["Count"], name=status,
                        marker_color=STATUS_COLORS.get(status, "#64748b"),
                    ))
            fig_bar.update_layout(
                barmode="stack", height=360,
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center", font=dict(size=11)),
                xaxis_title="", yaxis_title="",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            fig_bar.update_yaxes(gridcolor="#f0f0f0")
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Single run — comparison not available.")

    st.markdown("")

    # ── Detail tables ──
    st.markdown("##### Test Details")

    for status in order:
        grp = dff[dff["Status"] == status]
        cnt = len(grp)
        if cnt == 0:
            continue

        color = STATUS_COLORS.get(status, "#64748b")
        desc = STATUS_DESCRIPTIONS.get(status, "")

        with st.expander(f"{status} — {cnt} tests"):
            st.caption(desc)

            cols = ["Case ID", "Title", "Priority", "Type", "Run"]
            if status == "Not Applicable":
                cols.append("Review Notes")

            show = grp[cols + ["Link"]].copy()
            st.dataframe(
                show,
                column_config={
                    "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                    "Case ID": st.column_config.NumberColumn("Case ID", format="%d"),
                },
                hide_index=True,
                use_container_width=True,
            )

    # ── Legend ──
    st.markdown("")
    st.markdown("##### Legend")
    legend_md = ""
    for s in order:
        c = STATUS_COLORS.get(s, "#64748b")
        d = STATUS_DESCRIPTIONS.get(s, "")
        legend_md += f"- **{s}** — {d}\n"
    st.markdown(legend_md)


if __name__ == "__main__":
    main()
