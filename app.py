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
    "Drogas": {"plan_id": 62842},
}

# Custom field names — adjust if different in your TestRail instance
FIELD_REVIEW_NOTES = "custom_review_note"
FIELD_NA_REASON = "custom_automation_not_applicable_reason"
FIELD_COUNTRIES = "custom_multi_countries"
FIELD_DEVICE = "custom_device"
FIELD_TESTIM_DESKTOP = "custom_automation_status_testim_desktop"
FIELD_TESTIM_MOBILE = "custom_automation_status_testim_mobile_view"


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
        tests, offset, limit = [], 0, 250
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

    def get_case_fields(self) -> list[dict]:
        return self._get("get_case_fields")


# ─────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_plan_data(plan_id: int):
    client = TestRailClient(TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY)
    plan = client.get_plan(plan_id)
    statuses_raw = client.get_statuses()
    priorities_raw = client.get_priorities()
    case_types_raw = client.get_case_types()
    case_fields = client.get_case_fields()

    status_map = {s["id"]: s["label"] for s in statuses_raw}
    priority_map = {p["id"]: p["name"] for p in priorities_raw}
    type_map = {t["id"]: t["name"] for t in case_types_raw}

    # Build dropdown option maps for custom fields
    dropdown_maps = {}
    for field in case_fields:
        sys_name = f"custom_{field.get('system_name', field.get('name', ''))}"
        if field.get("type_id") in (6, 12):  # dropdown or multi-select
            for cfg in field.get("configs", []):
                options = cfg.get("options", {})
                items_str = options.get("items", "")
                if items_str:
                    opt_map = {}
                    for line in items_str.split("\n"):
                        line = line.strip()
                        if "," in line:
                            val, label = line.split(",", 1)
                            opt_map[int(val.strip())] = label.strip()
                    dropdown_maps[sys_name] = opt_map

    runs_info = []
    all_tests = []
    for entry in plan.get("entries", []):
        for run in entry.get("runs", []):
            run_id = run["id"]
            run_name = run["name"]
            runs_info.append({
                "run_id": run_id,
                "run_name": run_name,
                "run_url": run.get("url", ""),
            })
            tests = client.get_tests(run_id)
            for t in tests:
                t["_run_name"] = run_name
                t["_run_id"] = run_id
            all_tests.extend(tests)

    return plan, runs_info, all_tests, status_map, priority_map, type_map, dropdown_maps


# ─────────────────────────────────────────────────────────────
# Status helpers
# ─────────────────────────────────────────────────────────────
STATUS_GROUP_MAP = {
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
    "No-Run": "#cbd5e1",
    "Not automated": "#a78bfa",
    "Automation not applicable": "#64748b",
}

STATUS_DESCRIPTIONS = {
    "Passed": "Merged into the master branch.",
    "Passed with Issue": "PR raised — review in progress.",
    "Passed with Stub": "Implementation completed — PR yet to be raised.",
    "To Do": "Picked by team.",
    "Blocked": "Blocked due to an issue, pending test data, or other dependencies.",
    "Failed": "Test execution failed.",
    "Failed (Medium)": "Automation not feasible on UAT; will be revisited later.",
    "Not Applicable": "Excluded from automation.",
    "Untested": "Not yet executed.",
    "No-Run": "Not yet executed.",
    "Not automated": "Pending automation.",
    "Automation not applicable": "Automation not applicable for this test.",
}

BASE_STATUS_ORDER = [
    "Passed", "Passed with Issue", "Passed with Stub",
    "To Do", "Blocked", "Failed", "Failed (Medium)",
    "Not automated", "Automation not applicable",
    "Untested", "No-Run", "Not Applicable",
]


def resolve_status(status_label: str) -> str:
    return STATUS_GROUP_MAP.get(status_label, status_label)


def get_status_order(present: list[str]) -> list[str]:
    order = [s for s in BASE_STATUS_ORDER if s in present]
    for s in present:
        if s not in order:
            order.append(s)
    return order


def resolve_custom_field(raw_value, dropdown_map: dict | None) -> str:
    """Resolve a custom field value — handles dropdown IDs and multi-select lists."""
    if raw_value is None:
        return ""
    if dropdown_map:
        if isinstance(raw_value, list):
            return ", ".join(dropdown_map.get(v, str(v)) for v in raw_value)
        if isinstance(raw_value, int):
            return dropdown_map.get(raw_value, str(raw_value))
    if isinstance(raw_value, list):
        return ", ".join(str(v) for v in raw_value)
    return str(raw_value) if raw_value else ""


def build_testrail_url(case_id) -> str:
    return f"{TESTRAIL_URL}/index.php?/cases/view/{case_id}" if case_id else ""


# ─────────────────────────────────────────────────────────────
# DataFrame builder
# ─────────────────────────────────────────────────────────────
def build_dataframe(all_tests, status_map, priority_map, type_map, dropdown_maps):
    rows = []
    for t in all_tests:
        status_label = status_map.get(t.get("status_id"), "Unknown")
        status = resolve_status(status_label)

        # Resolve custom fields
        na_reason = resolve_custom_field(
            t.get(FIELD_NA_REASON),
            dropdown_maps.get(FIELD_NA_REASON),
        )
        countries_raw = resolve_custom_field(
            t.get(FIELD_COUNTRIES),
            dropdown_maps.get(FIELD_COUNTRIES),
        )
        device = resolve_custom_field(
            t.get(FIELD_DEVICE),
            dropdown_maps.get(FIELD_DEVICE),
        ) or "Both"
        testim_desktop = resolve_custom_field(
            t.get(FIELD_TESTIM_DESKTOP),
            dropdown_maps.get(FIELD_TESTIM_DESKTOP),
        )
        testim_mobile = resolve_custom_field(
            t.get(FIELD_TESTIM_MOBILE),
            dropdown_maps.get(FIELD_TESTIM_MOBILE),
        )
        review_notes = t.get(FIELD_REVIEW_NOTES, "") or ""

        # Parse countries
        countries = [c.strip() for c in countries_raw.replace(",", "\n").split("\n") if c.strip()]
        has_lt = "LT" in countries
        has_lv = "LV" in countries
        has_both = has_lt and has_lv

        # Determine effective status considering Testim fields
        # If both Testim Desktop and Mobile say "not applicable" variants, count as Not Applicable
        na_variants = {"not applicable", "automation not applicable", "n/a"}
        desktop_na = testim_desktop.strip().lower() in na_variants
        mobile_na = testim_mobile.strip().lower() in na_variants

        # For device=Desktop, only check desktop; for Mobile only mobile; for Both check both
        if device == "Desktop" and desktop_na:
            effective_status = "Not Applicable"
        elif device == "Mobile" and mobile_na:
            effective_status = "Not Applicable"
        elif device == "Both" and desktop_na and mobile_na:
            effective_status = "Not Applicable"
        else:
            effective_status = status

        rows.append({
            "Case ID": t.get("case_id"),
            "Title": t.get("title", ""),
            "Status (Raw)": status_label,
            "Status": effective_status,
            "Priority": priority_map.get(t.get("priority_id"), "—"),
            "Type": type_map.get(t.get("type_id"), "—"),
            "Run": t.get("_run_name", ""),
            "Device": device,
            "Countries": ", ".join(countries) if countries else "—",
            "LT": has_lt,
            "LV": has_lv,
            "Both Countries": has_both,
            "NA Reason": na_reason,
            "Review Notes": review_notes,
            "Testim Desktop": testim_desktop,
            "Testim Mobile": testim_mobile,
            "Link": build_testrail_url(t.get("case_id")),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# Rendering components
# ─────────────────────────────────────────────────────────────
def render_kpi_strip(order, counts, total):
    """Render a Plotly indicator strip for KPI numbers."""
    fig = go.Figure()
    n = len(order)
    for i, status in enumerate(order):
        c = int(counts.get(status, 0))
        pct = c / total * 100 if total else 0
        color = STATUS_COLORS.get(status, "#64748b")
        fig.add_trace(go.Indicator(
            mode="number",
            value=c,
            number=dict(font=dict(size=32, color=color)),
            title=dict(
                text=f"<b>{status}</b><br><span style='font-size:0.7em;color:#888'>{pct:.1f}%</span>",
                font=dict(size=11),
            ),
            domain=dict(x=[i / n + 0.003, (i + 1) / n - 0.003], y=[0, 1]),
        ))
    fig.update_layout(
        height=110, margin=dict(t=28, b=0, l=5, r=5),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_progress_bar(done, actionable, na):
    """Render a Plotly horizontal stacked bar as progress indicator."""
    pct = done / actionable * 100 if actionable else 0
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[pct], y=[""], orientation="h",
        marker=dict(color="#059669", cornerradius=6),
        text=f"  {pct:.1f}%", textposition="inside",
        textfont=dict(color="white", size=14), hoverinfo="skip",
    ))
    fig.add_trace(go.Bar(
        x=[100 - pct], y=[""], orientation="h",
        marker=dict(color="#e2e8f0", cornerradius=6),
        hoverinfo="skip", showlegend=False,
    ))
    fig.update_layout(
        barmode="stack", height=48,
        margin=dict(t=0, b=0, l=0, r=0),
        xaxis=dict(visible=False, range=[0, 100]),
        yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption(
        f"**{done}** / **{actionable}** actionable tests automated · "
        f"{na} not applicable excluded"
    )


def render_breakdown_table(dff, order, counts, label_col, labels):
    """Render a breakdown table (by device or country)."""
    data = {"Status": [], **{l: [] for l in labels}, "Total": []}
    for status in order:
        cnt = int(counts.get(status, 0))
        if cnt == 0:
            continue
        data["Status"].append(status)
        data["Total"].append(cnt)
        grp = dff[dff["Status"] == status]
        for l in labels:
            if label_col == "Device":
                n = len(grp[grp["Device"] == l])
            elif l in ("LT", "LV"):
                n = len(grp[grp[l] == True])
            elif l == "Both":
                n = len(grp[grp["Both Countries"] == True])
            else:
                n = 0
            data[l].append(n)
    # Totals row
    data["Status"].append("**Total**")
    data["Total"].append(sum(data["Total"]))
    for l in labels:
        data[l].append(sum(data[l]))
    tdf = pd.DataFrame(data)
    st.dataframe(tdf, hide_index=True, use_container_width=True)


def render_na_reasons(dff):
    """Render Not Applicable reasons breakdown."""
    na_df = dff[dff["Status"] == "Not Applicable"].copy()
    if na_df.empty:
        return

    # Also include tests where NA Reason is populated regardless of status
    reason_df = dff[dff["NA Reason"].str.strip() != ""].copy()
    if reason_df.empty and na_df.empty:
        return

    combined = pd.concat([na_df, reason_df]).drop_duplicates(subset=["Case ID", "Run"])

    st.markdown("##### Not Applicable Reasons")
    reason_counts = combined["NA Reason"].replace("", "No reason specified").value_counts()

    fig = go.Figure(go.Bar(
        x=reason_counts.values,
        y=reason_counts.index,
        orientation="h",
        marker_color="#64748b",
        text=reason_counts.values,
        textposition="auto",
    ))
    fig.update_layout(
        height=max(200, len(reason_counts) * 36),
        margin=dict(t=10, b=10, l=10, r=10),
        xaxis_title="", yaxis_title="",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(autorange="reversed"),
    )
    fig.update_xaxes(gridcolor="#f0f0f0")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Detail table grouped by reason
    for reason in reason_counts.index:
        actual_reason = "" if reason == "No reason specified" else reason
        sub = combined[combined["NA Reason"] == actual_reason] if actual_reason else combined[combined["NA Reason"].str.strip() == ""]
        with st.expander(f"{reason} — {len(sub)} tests"):
            show = sub[["Case ID", "Title", "Priority", "Type", "Run", "Device", "Countries", "Link"]].copy()
            st.dataframe(
                show,
                column_config={
                    "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                    "Case ID": st.column_config.NumberColumn(format="%d"),
                },
                hide_index=True, use_container_width=True,
            )


def render_detail_tables(dff, order):
    """Render expandable detail tables per status."""
    st.markdown("##### Test Details")
    for status in order:
        grp = dff[dff["Status"] == status]
        cnt = len(grp)
        if cnt == 0:
            continue
        desc = STATUS_DESCRIPTIONS.get(status, "")
        with st.expander(f"{status} — {cnt} tests"):
            st.caption(desc)
            cols = ["Case ID", "Title", "Priority", "Type", "Run", "Device", "Countries"]
            if status == "Not Applicable":
                cols.extend(["NA Reason", "Review Notes"])
            show = grp[cols + ["Link"]].copy()
            st.dataframe(
                show,
                column_config={
                    "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                    "Case ID": st.column_config.NumberColumn(format="%d"),
                },
                hide_index=True, use_container_width=True,
            )


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Automation Backlog", layout="wide")

    st.markdown("""<style>
    .block-container {padding-top:1.5rem; padding-bottom:1rem;}
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
            plan, runs_info, all_tests, status_map, priority_map, type_map, dropdown_maps = fetch_plan_data(plan_id)
        except Exception as e:
            st.error(f"Error: {e}")
            return

    df = build_dataframe(all_tests, status_map, priority_map, type_map, dropdown_maps)
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
    order = get_status_order(present)
    counts = dff["Status"].value_counts()

    # ── KPI Strip ──
    render_kpi_strip(order, counts, total)
    st.caption(f"**{total}** tests total across **{len(runs_info)}** run(s)")

    # ── Progress ──
    done_set = {"Passed", "Passed with Issue", "Passed with Stub"}
    done = sum(int(counts.get(s, 0)) for s in done_set)
    na = int(counts.get("Not Applicable", 0))
    actionable = total - na
    render_progress_bar(done, actionable, na)

    st.markdown("")

    # ── Breakdown tables ──
    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.markdown("##### Breakdown by Device")
        devices = sorted(dff["Device"].unique().tolist())
        render_breakdown_table(dff, order, counts, "Device", devices)

    with c2:
        # Country breakdown only if multi_countries data exists
        has_countries = dff["Countries"].str.strip().replace("—", "").any()
        if has_countries:
            st.markdown("##### Breakdown by Country")
            render_breakdown_table(dff, order, counts, "Country", ["LT", "LV", "Both"])
        else:
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
                height=340, margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center", font=dict(size=11)),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_pie, use_container_width=True, config={"displayModeBar": False})

    st.markdown("")

    # ── Charts ──
    c3, c4 = st.columns(2, gap="large")

    with c3:
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

    with c4:
        st.markdown("##### Status by Run")
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
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            )
            fig_bar.update_yaxes(gridcolor="#f0f0f0")
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Single run — comparison not available.")

    st.markdown("")

    # ── NA Reasons ──
    render_na_reasons(dff)

    st.markdown("")

    # ── Detail tables ──
    render_detail_tables(dff, order)

    # ── Legend ──
    st.markdown("")
    st.markdown("##### Legend")
    legend_md = ""
    for s in order:
        d = STATUS_DESCRIPTIONS.get(s, "")
        legend_md += f"- **{s}** — {d}\n"
    st.markdown(legend_md)


if __name__ == "__main__":
    main()
