from typing import Optional, List

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

FIELD_REVIEW_NOTES = "custom_review_note"
FIELD_NA_REASON = "custom_automation_not_applicable_reason"
FIELD_COUNTRIES = "custom_multi_countries"
FIELD_DEVICE = "custom_device"
FIELD_TESTIM_DESKTOP = "custom_automation_status_testim_desktop"
FIELD_TESTIM_MOBILE = "custom_automation_status_testim_mobile_view"

# Dark theme palette
BG = "#0f1117"
CARD_BG = "#1a1c2e"
TEXT = "#e2e8f0"
TEXT_DIM = "#94a3b8"
BORDER = "#2d3148"
ACCENT = "#6366f1"


# ─────────────────────────────────────────────────────────────
# TestRail API client
# ─────────────────────────────────────────────────────────────
class TestRailClient:
    def __init__(self, base_url: str, user: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, api_key)
        self.session.headers.update({"Content-Type": "application/json"})

    def _get(self, endpoint: str, params: Optional[dict] = None):
        url = f"{self.base_url}/index.php?/api/v2/{endpoint}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}&{query}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_plan(self, plan_id: int) -> dict:
        return self._get(f"get_plan/{plan_id}")

    def get_tests(self, run_id: int) -> List[dict]:
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

    def get_statuses(self) -> List[dict]:
        return self._get("get_statuses")

    def get_priorities(self) -> List[dict]:
        return self._get("get_priorities")

    def get_case_types(self) -> List[dict]:
        return self._get("get_case_types")

    def get_case_fields(self) -> List[dict]:
        return self._get("get_case_fields")


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

    dropdown_maps = {}
    for field in case_fields:
        sys_name = f"custom_{field.get('system_name', field.get('name', ''))}"
        if field.get("type_id") in (6, 12):
            for cfg in field.get("configs", []):
                items_str = cfg.get("options", {}).get("items", "")
                if items_str:
                    opt_map = {}
                    for line in items_str.split("\n"):
                        line = line.strip()
                        if "," in line:
                            val, label = line.split(",", 1)
                            opt_map[int(val.strip())] = label.strip()
                    dropdown_maps[sys_name] = opt_map

    runs_info, all_tests = [], []
    for entry in plan.get("entries", []):
        for run in entry.get("runs", []):
            rid, rname = run["id"], run["name"]
            runs_info.append({"run_id": rid, "run_name": rname, "run_url": run.get("url", "")})
            tests = client.get_tests(rid)
            for t in tests:
                t["_run_name"] = rname
                t["_run_id"] = rid
            all_tests.extend(tests)

    return plan, runs_info, all_tests, status_map, priority_map, type_map, dropdown_maps


# ─────────────────────────────────────────────────────────────
# Status config
# ─────────────────────────────────────────────────────────────
STATUS_GROUP_MAP = {
    "Passed": "Passed", "Passed with Issue": "Passed with Issue",
    "Passed with Stub": "Passed with Stub", "To Do": "To Do", "To-do": "To Do",
    "Blocked": "Blocked", "Failed (Medium)": "Failed (Medium)",
    "Not Applicable": "Not Applicable", "Failed": "Failed",
    "Retest": "To Do", "Untested": "Untested",
}

STATUS_COLORS = {
    "Passed": "#34d399", "Passed with Issue": "#6ee7b7",
    "Passed with Stub": "#a3e635", "To Do": "#818cf8",
    "Blocked": "#fb923c", "Failed": "#f87171",
    "Failed (Medium)": "#fbbf24", "Not Applicable": "#64748b",
    "Untested": "#94a3b8", "No-Run": "#64748b",
    "Not automated": "#c4b5fd", "Automation not applicable": "#64748b",
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
    "Untested": "Not yet executed.", "No-Run": "Not yet executed.",
    "Not automated": "Pending automation.",
    "Automation not applicable": "Automation not applicable for this test.",
}

BASE_STATUS_ORDER = [
    "Passed", "Passed with Issue", "Passed with Stub",
    "To Do", "Blocked", "Failed", "Failed (Medium)",
    "Not automated", "Automation not applicable",
    "Untested", "No-Run", "Not Applicable",
]


def resolve_status(label: str) -> str:
    return STATUS_GROUP_MAP.get(label, label)


def get_status_order(present: List[str]) -> List[str]:
    order = [s for s in BASE_STATUS_ORDER if s in present]
    for s in present:
        if s not in order:
            order.append(s)
    return order


def resolve_custom_field(raw, dmap: Optional[dict]) -> str:
    if raw is None:
        return ""
    if dmap:
        if isinstance(raw, list):
            return ", ".join(dmap.get(v, str(v)) for v in raw)
        if isinstance(raw, int):
            return dmap.get(raw, str(raw))
    if isinstance(raw, list):
        return ", ".join(str(v) for v in raw)
    return str(raw) if raw else ""


def build_testrail_url(case_id) -> str:
    return f"{TESTRAIL_URL}/index.php?/cases/view/{case_id}" if case_id else ""


# ─────────────────────────────────────────────────────────────
# DataFrame builder
# ─────────────────────────────────────────────────────────────
def build_dataframe(all_tests, status_map, priority_map, type_map, dropdown_maps):
    rows = []
    na_variants = {"not applicable", "automation not applicable", "n/a"}
    for t in all_tests:
        status_label = status_map.get(t.get("status_id"), "Unknown")
        status = resolve_status(status_label)

        na_reason = resolve_custom_field(t.get(FIELD_NA_REASON), dropdown_maps.get(FIELD_NA_REASON))
        countries_raw = resolve_custom_field(t.get(FIELD_COUNTRIES), dropdown_maps.get(FIELD_COUNTRIES))
        device = resolve_custom_field(t.get(FIELD_DEVICE), dropdown_maps.get(FIELD_DEVICE)) or "Both"
        testim_d = resolve_custom_field(t.get(FIELD_TESTIM_DESKTOP), dropdown_maps.get(FIELD_TESTIM_DESKTOP))
        testim_m = resolve_custom_field(t.get(FIELD_TESTIM_MOBILE), dropdown_maps.get(FIELD_TESTIM_MOBILE))
        review_notes = t.get(FIELD_REVIEW_NOTES, "") or ""

        countries = [c.strip() for c in countries_raw.replace(",", "\n").split("\n") if c.strip()]
        has_lt, has_lv = "LT" in countries, "LV" in countries

        d_na = testim_d.strip().lower() in na_variants
        m_na = testim_m.strip().lower() in na_variants
        if (device == "Desktop" and d_na) or (device == "Mobile" and m_na) or (device == "Both" and d_na and m_na):
            effective = "Not Applicable"
        else:
            effective = status

        rows.append({
            "Case ID": t.get("case_id"), "Title": t.get("title", ""),
            "Status": effective, "Priority": priority_map.get(t.get("priority_id"), "—"),
            "Type": type_map.get(t.get("type_id"), "—"), "Run": t.get("_run_name", ""),
            "Device": device, "Countries": ", ".join(countries) if countries else "—",
            "LT": has_lt, "LV": has_lv, "Both Countries": has_lt and has_lv,
            "NA Reason": na_reason, "Review Notes": review_notes,
            "Testim Desktop": testim_d, "Testim Mobile": testim_m,
            "Link": build_testrail_url(t.get("case_id")),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# Plotly defaults for dark theme
# ─────────────────────────────────────────────────────────────
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
)
PLOTLY_CFG = {"displayModeBar": False}


# ─────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────
def render_kpi_strip(order, counts, total):
    n = len(order)
    if n == 0:
        return
    fig = go.Figure()
    for i, s in enumerate(order):
        c = int(counts.get(s, 0))
        pct = c / total * 100 if total else 0
        color = STATUS_COLORS.get(s, "#64748b")
        fig.add_trace(go.Indicator(
            mode="number", value=c,
            number=dict(font=dict(size=34, color=color)),
            title=dict(
                text=f"<b style='color:{TEXT}'>{s}</b><br>"
                     f"<span style='font-size:0.75em;color:{TEXT_DIM}'>{pct:.1f}%</span>",
                font=dict(size=11),
            ),
            domain=dict(x=[i / n + 0.005, (i + 1) / n - 0.005], y=[0, 1]),
        ))
    fig.update_layout(height=115, margin=dict(t=30, b=0, l=5, r=5), **PLOTLY_LAYOUT)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)


def render_progress(done, actionable, na):
    pct = done / actionable * 100 if actionable else 0
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[pct], y=[""], orientation="h",
        marker=dict(color="#34d399", cornerradius=8),
        text=f"  {pct:.1f}%", textposition="inside",
        textfont=dict(color="#0f1117", size=14, family="Inter, sans-serif"),
        hoverinfo="skip",
    ))
    fig.add_trace(go.Bar(
        x=[100 - pct], y=[""], orientation="h",
        marker=dict(color=BORDER, cornerradius=8),
        hoverinfo="skip", showlegend=False,
    ))
    fig.update_layout(
        barmode="stack", height=44,
        margin=dict(t=0, b=0, l=0, r=0),
        xaxis=dict(visible=False, range=[0, 100]),
        yaxis=dict(visible=False), showlegend=False, **PLOTLY_LAYOUT,
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)
    st.caption(
        f"**{done}** / **{actionable}** actionable tests automated  ·  "
        f"{na} not applicable excluded"
    )


def render_breakdown_table(dff, order, counts, label_col, labels):
    data = {"Status": [], **{la: [] for la in labels}, "Total": []}
    for s in order:
        cnt = int(counts.get(s, 0))
        if cnt == 0:
            continue
        data["Status"].append(s)
        data["Total"].append(cnt)
        grp = dff[dff["Status"] == s]
        for la in labels:
            if label_col == "Device":
                data[la].append(len(grp[grp["Device"] == la]))
            elif la in ("LT", "LV"):
                data[la].append(len(grp[grp[la] == True]))
            elif la == "Both":
                data[la].append(len(grp[grp["Both Countries"] == True]))
            else:
                data[la].append(0)
    data["Status"].append("Total")
    data["Total"].append(sum(data["Total"]))
    for la in labels:
        data[la].append(sum(data[la]))
    st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)


def render_na_reasons(dff):
    na_df = dff[dff["Status"] == "Not Applicable"].copy()
    reason_df = dff[dff["NA Reason"].str.strip() != ""].copy()
    combined = pd.concat([na_df, reason_df]).drop_duplicates(subset=["Case ID", "Run"])
    if combined.empty:
        return

    st.markdown("#### Not Applicable Breakdown")
    reason_counts = combined["NA Reason"].replace("", "No reason specified").value_counts()

    fig = go.Figure(go.Bar(
        x=reason_counts.values, y=reason_counts.index,
        orientation="h", marker_color=ACCENT,
        text=reason_counts.values, textposition="auto",
        textfont=dict(color=TEXT),
    ))
    fig.update_layout(
        height=max(180, len(reason_counts) * 40),
        margin=dict(t=5, b=5, l=5, r=5),
        xaxis=dict(visible=False), yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
        **PLOTLY_LAYOUT,
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)

    for reason in reason_counts.index:
        actual = "" if reason == "No reason specified" else reason
        sub = combined[combined["NA Reason"] == actual] if actual else combined[combined["NA Reason"].str.strip() == ""]
        with st.expander(f"{reason} — {len(sub)} tests"):
            show = sub[["Case ID", "Title", "Priority", "Type", "Run", "Device", "Countries", "Link"]].copy()
            st.dataframe(show, column_config={
                "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                "Case ID": st.column_config.NumberColumn(format="%d"),
            }, hide_index=True, use_container_width=True)


def render_detail_tables(dff, order):
    st.markdown("#### Test Details")
    for s in order:
        grp = dff[dff["Status"] == s]
        cnt = len(grp)
        if cnt == 0:
            continue
        desc = STATUS_DESCRIPTIONS.get(s, "")
        with st.expander(f"{s} — {cnt} tests"):
            st.caption(desc)
            cols = ["Case ID", "Title", "Priority", "Type", "Run", "Device", "Countries"]
            if s == "Not Applicable":
                cols.extend(["NA Reason", "Review Notes"])
            show = grp[cols + ["Link"]].copy()
            st.dataframe(show, column_config={
                "Link": st.column_config.LinkColumn("TestRail", display_text="Open"),
                "Case ID": st.column_config.NumberColumn(format="%d"),
            }, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Automation Backlog", layout="wide")

    # ── Inject dark theme CSS ──
    _css = (
        "<style>"
        ".block-container { padding-top: 1.2rem; padding-bottom: 1rem; }"
        "#MainMenu, footer, header { visibility: hidden; }"
        "div[data-testid='stExpander'] {"
        "  border: 1px solid " + BORDER + ";"
        "  border-radius: 8px;"
        "  margin-bottom: 6px;"
        "}"
        "div[data-testid='stExpander'] details summary span {"
        "  font-size: 0.9rem; font-weight: 500;"
        "}"
        ".stDataFrame { border-radius: 8px; overflow: hidden; }"
        "hr { border-color: " + BORDER + " !important; opacity: 0.5; }"
        "</style>"
    )
    st.markdown(_css, unsafe_allow_html=True)

    if not all([TESTRAIL_URL, TESTRAIL_USER, TESTRAIL_API_KEY]):
        st.error("TestRail credentials not configured.")
        return

    # ── Header with filters inline ──
    st.markdown("## Automation Backlog")
    f1, f2, f3 = st.columns([2, 2, 6])
    with f1:
        bu_name = st.selectbox("Business Unit", list(BU_PLANS.keys()))

    plan_id = BU_PLANS[bu_name]["plan_id"]

    with st.spinner("Loading from TestRail..."):
        try:
            plan, runs_info, all_tests, status_map, priority_map, type_map, dd = fetch_plan_data(plan_id)
        except Exception as e:
            st.error(f"Error: {e}")
            return

    df = build_dataframe(all_tests, status_map, priority_map, type_map, dd)
    if df.empty:
        st.warning("No tests found.")
        return

    run_names = ["All Runs"] + sorted(df["Run"].unique().tolist())
    with f2:
        selected_run = st.selectbox("Run", run_names)

    plan_url = plan.get("url", f"{TESTRAIL_URL}/index.php?/plans/view/{plan_id}")
    runs_pills = "  ".join([f"`{r['run_name']}`" for r in runs_info])
    st.caption(f"{plan.get('name', '')}  ·  [Open in TestRail]({plan_url})  ·  {runs_pills}")
    dff = df[df["Run"] == selected_run].copy() if selected_run != "All Runs" else df.copy()
    total = len(dff)

    present = dff["Status"].unique().tolist()
    order = get_status_order(present)
    counts = dff["Status"].value_counts()

    # ── KPI ──
    render_kpi_strip(order, counts, total)

    col_total, col_spacer = st.columns([1, 3])
    with col_total:
        st.metric("Total Tests", total)

    st.divider()

    # ── Progress ──
    done_set = {"Passed", "Passed with Issue", "Passed with Stub"}
    done = sum(int(counts.get(s, 0)) for s in done_set)
    na = int(counts.get("Not Applicable", 0))
    actionable = total - na
    render_progress(done, actionable, na)

    st.divider()

    # ── Charts ──
    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.markdown("#### Status Distribution")
        cd = [{"s": s, "c": int(counts.get(s, 0))} for s in order if counts.get(s, 0)]
        fig = go.Figure(go.Pie(
            labels=[d["s"] for d in cd], values=[d["c"] for d in cd],
            marker=dict(colors=[STATUS_COLORS.get(d["s"], "#64748b") for d in cd],
                        line=dict(color=BG, width=2)),
            hole=0.55, textposition="inside", textinfo="value+percent",
            textfont=dict(size=11, color="#fff"), sort=False,
        ))
        fig.update_layout(
            height=380, margin=dict(t=10, b=10, l=10, r=10),
            legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center",
                        font=dict(size=11, color=TEXT_DIM)),
            **PLOTLY_LAYOUT,
        )
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CFG)

    with c2:
        st.markdown("#### Status by Run")
        if len(runs_info) > 1:
            rs = df.groupby(["Run", "Status"]).size().reset_index(name="Count")
            fig2 = go.Figure()
            for s in order:
                sub = rs[rs["Status"] == s]
                if not sub.empty:
                    fig2.add_trace(go.Bar(
                        x=sub["Run"], y=sub["Count"], name=s,
                        marker_color=STATUS_COLORS.get(s, "#64748b"),
                    ))
            fig2.update_layout(
                barmode="stack", height=380,
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center",
                            font=dict(size=11, color=TEXT_DIM)),
                xaxis=dict(title="", tickfont=dict(color=TEXT_DIM)),
                yaxis=dict(title="", gridcolor=BORDER, tickfont=dict(color=TEXT_DIM)),
                **PLOTLY_LAYOUT,
            )
            st.plotly_chart(fig2, use_container_width=True, config=PLOTLY_CFG)
        else:
            st.info("Single run — comparison not available.")

    st.divider()

    # ── Breakdowns ──
    has_countries = dff["Countries"].str.strip().replace("—", "").any()

    if has_countries:
        b1, b2 = st.columns(2, gap="large")
        with b1:
            st.markdown("#### By Device")
            devices = sorted(dff["Device"].unique().tolist())
            render_breakdown_table(dff, order, counts, "Device", devices)
        with b2:
            st.markdown("#### By Country")
            render_breakdown_table(dff, order, counts, "Country", ["LT", "LV", "Both"])
    else:
        st.markdown("#### By Device")
        devices = sorted(dff["Device"].unique().tolist())
        render_breakdown_table(dff, order, counts, "Device", devices)

    st.divider()

    # ── NA Reasons ──
    render_na_reasons(dff)

    st.divider()

    # ── Detail tables ──
    render_detail_tables(dff, order)

    # ── Legend ──
    st.divider()
    with st.expander("Legend"):
        for s in order:
            color = STATUS_COLORS.get(s, "#64748b")
            desc = STATUS_DESCRIPTIONS.get(s, "")
            st.markdown(f"- **{s}** — {desc}")


if __name__ == "__main__":
    main()
