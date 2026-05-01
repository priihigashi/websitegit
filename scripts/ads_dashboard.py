"""
ads_dashboard.py — Pull Google Ads data, regenerate docs/dashboard/index.html
Run by ads_pulse.yml every Monday 8 AM ET.
"""
import json, os, re, sys, smtplib, datetime, calendar
from email.message import EmailMessage
from pathlib import Path


def _build_periods():
    today = datetime.date.today()
    first_this = today.replace(day=1)
    first_prev = (first_this - datetime.timedelta(days=1)).replace(day=1)
    last_prev2 = first_prev - datetime.timedelta(days=1)
    first_prev2 = last_prev2.replace(day=1)
    def back(n):
        return f"segments.date BETWEEN '{today - datetime.timedelta(days=n)}' AND '{today}'"
    return {
        "15d":        "segments.date DURING LAST_14_DAYS",
        "30d":        "segments.date DURING LAST_30_DAYS",
        "60d":        back(60),
        "90d":        back(90),
        "6mo":        back(180),
        "12mo":       back(365),
        "cur_month":  "segments.date DURING THIS_MONTH",
        "prev_month": "segments.date DURING LAST_MONTH",
        "prev2_month":f"segments.date BETWEEN '{first_prev2}' AND '{last_prev2}'",
    }


PERIODS = _build_periods()

CAMPAIGN_ID = "23314409466"


def get_period_data(ga, customer_id, date_filter):
    r1 = ga.search(customer_id=customer_id, query=f"""
        SELECT campaign.name, metrics.clicks, metrics.impressions, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros, metrics.conversions,
               metrics.phone_calls, metrics.search_impression_share,
               metrics.search_top_impression_share
        FROM campaign
        WHERE {date_filter} AND campaign.id = {CAMPAIGN_ID}
    """)
    campaign = {}
    for row in r1:
        m = row.metrics
        is_pct = round(m.search_impression_share * 100, 1) if m.search_impression_share else None
        top_pct = round(m.search_top_impression_share * 100, 1) if m.search_top_impression_share else None
        campaign = {
            "clicks":      m.clicks,
            "impressions": m.impressions,
            "ctr":         round(m.ctr * 100, 2),
            "avg_cpc":     round(m.average_cpc / 1e6, 2),
            "spend":       round(m.cost_micros / 1e6, 2),
            "conversions": m.conversions,
            "calls":       m.phone_calls,
            "is_pct":      is_pct,
            "top_is_pct":  top_pct,
        }

    r2 = ga.search(customer_id=customer_id, query=f"""
        SELECT ad_group.name, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM ad_group
        WHERE {date_filter} AND campaign.id = {CAMPAIGN_ID}
        ORDER BY metrics.cost_micros DESC
    """)
    adgroups = [
        {"name": row.ad_group.name,
         "spend": round(row.metrics.cost_micros / 1e6, 2),
         "clicks": row.metrics.clicks,
         "conv": row.metrics.conversions}
        for row in r2
    ]

    r3 = ga.search(customer_id=customer_id, query=f"""
        SELECT ad_group.name, ad_group_criterion.keyword.text,
               ad_group_criterion.quality_info.quality_score,
               metrics.clicks, metrics.impressions, metrics.ctr,
               metrics.cost_micros, metrics.conversions, metrics.average_cpc
        FROM keyword_view
        WHERE {date_filter} AND campaign.id = {CAMPAIGN_ID}
          AND metrics.cost_micros > 0
        ORDER BY metrics.cost_micros DESC LIMIT 10
    """)
    keywords = []
    for row in r3:
        qs = 0
        try:
            qs = int(row.ad_group_criterion.quality_info.quality_score) or 0
        except Exception:
            qs = 0
        keywords.append({
            "text":   row.ad_group_criterion.keyword.text,
            "ag":     row.ad_group.name.split()[0],
            "spend":  round(row.metrics.cost_micros / 1e6, 2),
            "clicks": row.metrics.clicks,
            "ctr":    round(row.metrics.ctr * 100, 2),
            "conv":   row.metrics.conversions,
            "qs":     qs,
        })

    return campaign, adgroups, keywords


def _enum_name(v):
    """Proto-plus enums: prefer .name; otherwise strip 'EnumName.' prefix from str()."""
    n = getattr(v, "name", None)
    if n: return n
    s = str(v)
    return s.split(".", 1)[1] if "." in s else s


def get_config_data(ga, customer_id):
    """Pull campaign + ad-group config (budget, bidding strategy, max CPC) — drives 'why' clauses."""
    config = {"campaign": {}, "ad_groups": []}
    try:
        r = ga.search(customer_id=customer_id, query=f"""
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.bidding_strategy_type,
                   campaign_budget.amount_micros,
                   campaign.target_cpa.target_cpa_micros,
                   campaign.maximize_conversions.target_cpa_micros
            FROM campaign
            WHERE campaign.id = {CAMPAIGN_ID}
        """)
        for row in r:
            tcpa_targetcpa = 0
            tcpa_maxconv = 0
            try: tcpa_targetcpa = row.campaign.target_cpa.target_cpa_micros / 1e6
            except Exception: pass
            try: tcpa_maxconv = row.campaign.maximize_conversions.target_cpa_micros / 1e6
            except Exception: pass
            config["campaign"] = {
                "name":          row.campaign.name,
                "status":        _enum_name(row.campaign.status),
                "bid_strategy":  _enum_name(row.campaign.bidding_strategy_type),
                "daily_budget":  round(row.campaign_budget.amount_micros / 1e6, 2),
                "target_cpa":    round(max(tcpa_targetcpa, tcpa_maxconv), 2),
            }
    except Exception as e:
        print(f"  config (campaign) failed: {e}")

    try:
        r = ga.search(customer_id=customer_id, query=f"""
            SELECT ad_group.id, ad_group.name, ad_group.status,
                   ad_group.cpc_bid_micros, ad_group.target_cpa_micros
            FROM ad_group
            WHERE campaign.id = {CAMPAIGN_ID}
        """)
        for row in r:
            config["ad_groups"].append({
                "id":        str(row.ad_group.id),
                "name":      row.ad_group.name,
                "status":    _enum_name(row.ad_group.status),
                "max_cpc":   round(row.ad_group.cpc_bid_micros / 1e6, 2) if row.ad_group.cpc_bid_micros else 0,
                "tcpa":      round(row.ad_group.target_cpa_micros / 1e6, 2) if row.ad_group.target_cpa_micros else 0,
            })
    except Exception as e:
        print(f"  config (ad_groups) failed: {e}")
    return config


def get_change_log(ga, customer_id):
    """Pull recent change events for the campaign. API hard limit: start date <30 days ago."""
    events = []
    today = datetime.date.today()
    start = today - datetime.timedelta(days=29)
    # NOTE: do NOT filter by change_event.campaign — bulk Google Ads Scripts often
    # populate only the most-specific resource path (ad_group / criterion), leaving
    # campaign empty. Filtering on it silently drops those changes. The customer_id
    # scope already constrains us to the OPC sub-account.
    expected_camp_path = f"customers/{customer_id}/campaigns/{CAMPAIGN_ID}"
    raw_count = 0
    try:
        r = ga.search(customer_id=customer_id, query=f"""
            SELECT change_event.change_date_time,
                   change_event.change_resource_type,
                   change_event.user_email,
                   change_event.client_type,
                   change_event.resource_change_operation,
                   change_event.changed_fields,
                   change_event.campaign,
                   change_event.ad_group
            FROM change_event
            WHERE change_event.change_date_time >= '{start} 00:00:00'
              AND change_event.change_date_time <= '{today} 23:59:59'
            ORDER BY change_event.change_date_time DESC
            LIMIT 500
        """)
        for row in r:
            ce = row.change_event
            raw_count += 1
            # Keep events scoped to our campaign when campaign field is set;
            # keep events with empty campaign too (bulk scripts may omit it).
            camp = str(ce.campaign or "")
            if camp and camp != expected_camp_path:
                continue
            try:
                fields_raw = str(ce.changed_fields)
                fields = fields_raw.replace("paths: ", "").replace('"', "").strip()
            except Exception:
                fields = ""
            events.append({
                "ts":     str(ce.change_date_time)[:19],
                "type":   _enum_name(ce.change_resource_type),
                "op":     _enum_name(ce.resource_change_operation),
                "user":   ce.user_email or "",
                "client": _enum_name(ce.client_type),
                "fields": fields[:200],
            })
    except Exception as e:
        print(f"  change_log failed: {e}")
    print(f"  change_log: {raw_count} raw events fetched, {len(events)} kept after campaign-scope filter")
    return events


def get_call_log(ga, customer_id):
    """Pull all available call details. No date filter — call_view doesn't support segments.date."""
    r = ga.search(customer_id=customer_id, query=f"""
        SELECT call_view.call_status, call_view.call_duration_seconds,
               call_view.start_call_date_time, call_view.caller_area_code,
               ad_group.name
        FROM call_view
        WHERE campaign.id = {CAMPAIGN_ID}
        ORDER BY call_view.start_call_date_time DESC
        LIMIT 200
    """)
    calls = []
    for row in r:
        cv = row.call_view
        dt_str = str(cv.start_call_date_time)
        try:
            dt = datetime.datetime.fromisoformat(dt_str.replace(" ", "T").split("+")[0])
            date_s = dt.strftime("%Y-%m-%d")
            time_s = dt.strftime("%H:%M")
        except Exception:
            date_s = dt_str[:10]
            time_s = dt_str[11:16]
        area = str(cv.caller_area_code) if cv.caller_area_code else "???"
        calls.append({
            "date":     date_s,
            "time":     time_s,
            "area":     area,
            "duration": int(cv.call_duration_seconds),
            "status":   _enum_name(cv.call_status),
            "ag":       row.ad_group.name,
        })
    return calls


def get_dayofweek_data(ga, customer_id):
    """Pull last-30d spend + calls broken out by day of week. Used for day-parting warnings."""
    # Google Ads DayOfWeek enum is 1-based: 1=MONDAY … 7=SUNDAY (0=UNSPECIFIED)
    INT_TO_DAY = {1:"Monday",2:"Tuesday",3:"Wednesday",4:"Thursday",5:"Friday",6:"Saturday",7:"Sunday"}
    STR_TO_DAY = {v.upper(): v for v in INT_TO_DAY.values()}  # {"MONDAY":"Monday", ...}
    dow = {}
    try:
        r = ga.search(customer_id=customer_id, query=f"""
            SELECT segments.day_of_week, metrics.cost_micros, metrics.phone_calls, metrics.clicks
            FROM campaign
            WHERE segments.date DURING LAST_30_DAYS AND campaign.id = {CAMPAIGN_ID}
        """)
        for row in r:
            raw = _enum_name(row.segments.day_of_week)  # "MONDAY" or bare int string
            name = (STR_TO_DAY.get(raw.upper())
                    or INT_TO_DAY.get(getattr(row.segments.day_of_week, "value", None))
                    or raw)
            if name not in dow:
                dow[name] = {"spend": 0, "calls": 0, "clicks": 0}
            dow[name]["spend"]  = round(dow[name]["spend"]  + row.metrics.cost_micros / 1e6, 2)
            dow[name]["calls"]  += row.metrics.phone_calls
            dow[name]["clicks"] += row.metrics.clicks
    except Exception as e:
        print(f"  dayofweek failed: {e}")
    # Return Mon–Sun ordered, only days that had actual data
    order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    return [{"day": d, **dow[d]} for d in order if d in dow]


def get_recent_daily_data(ga, customer_id):
    """Pull last 7 days of daily spend/clicks/impressions. Used for 2-3 day anomaly detection."""
    rows = []
    today = datetime.date.today()
    start = today - datetime.timedelta(days=6)
    try:
        r = ga.search(customer_id=customer_id, query=f"""
            SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.impressions
            FROM campaign
            WHERE segments.date BETWEEN '{start}' AND '{today}'
            AND campaign.id = {CAMPAIGN_ID}
            ORDER BY segments.date DESC
        """)
        date_map = {}
        for row in r:
            date_map[row.segments.date] = {
                "date":        row.segments.date,
                "spend":       round(row.metrics.cost_micros / 1e6, 2),
                "clicks":      row.metrics.clicks,
                "impressions": row.metrics.impressions,
            }
        # Fill in any missing dates as $0 (campaign was off or no data)
        for i in range(7):
            d = str(today - datetime.timedelta(days=i))
            rows.append(date_map.get(d, {"date": d, "spend": 0.0, "clicks": 0, "impressions": 0}))
    except Exception as e:
        print(f"  recent_daily failed: {e}")
    return rows


def get_all_data(customer_id, config):
    from google.ads.googleads.client import GoogleAdsClient
    client = GoogleAdsClient.load_from_dict(config)
    ga = client.get_service("GoogleAdsService")

    periods_data = {}
    for label, date_filter in PERIODS.items():
        print(f"  Pulling {label}...")
        camp, ags, kws = get_period_data(ga, customer_id, date_filter)
        periods_data[label] = {"campaign": camp, "adgroups": ags, "keywords": kws}

    print("  Pulling call log...")
    calls = get_call_log(ga, customer_id)

    print("  Pulling day-of-week breakdown...")
    dow_data = get_dayofweek_data(ga, customer_id)

    print("  Pulling config (budget, bid strategy, max CPC)...")
    config_data = get_config_data(ga, customer_id)

    print("  Pulling change log (last 30d)...")
    change_log = get_change_log(ga, customer_id)

    print("  Pulling recent daily data (last 7d for anomaly detection)...")
    recent_daily = get_recent_daily_data(ga, customer_id)

    return periods_data, calls, config_data, change_log, dow_data, recent_daily


def inject_data(html_path, data):
    html = Path(html_path).read_text()
    new_block = "const DATA = " + json.dumps(data, indent=2) + ";"
    # lambda prevents regex engine from misinterpreting JSON backslashes as backreferences
    html = re.sub(r"const DATA = \{.*?\};", lambda m: new_block, html, flags=re.DOTALL)
    Path(html_path).write_text(html)


def send_email(subject, body, gmail_password):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = "priscila@oakpark-construction.com"
    msg["To"]      = "priscila@oakpark-construction.com"
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login("priscila@oakpark-construction.com", gmail_password)
        s.send_message(msg)


def build_mom_insight(periods_data, change_log):
    """Evidence-driven MoM analysis — mirrors investigateMOM() JS logic.
    Investigation runs first; only concrete findings surface in output."""
    c = periods_data.get("cur_month", {}).get("campaign", {})
    p = periods_data.get("prev_month", {}).get("campaign", {})
    if not c or not p:
        return None

    # Observation
    calls_delta = int(c.get("calls", 0)) - int(p.get("calls", 0))
    spend_delta = c.get("spend", 0) - p.get("spend", 0)
    obs_parts = []
    if abs(calls_delta) >= 1:
        arrow = "↑" if calls_delta > 0 else "↓"
        obs_parts.append(f"calls {arrow}{abs(calls_delta)} ({int(p.get('calls',0))} → {int(c.get('calls',0))})")
    if abs(spend_delta) >= 10:
        arrow = "↑" if spend_delta > 0 else "↓"
        obs_parts.append(f"spend {arrow}${abs(spend_delta):.0f} (${p.get('spend',0):.0f} → ${c.get('spend',0):.0f})")
    obs = "; ".join(obs_parts) if obs_parts else "No significant MoM change"

    # Investigation 1 — change_log (last 45 days)
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=45)
    recent = []
    for entry in change_log:
        try:
            ts = datetime.datetime.fromisoformat(entry.get("ts", "")[:19])
            if ts >= cutoff:
                recent.append((ts, entry))
        except Exception:
            pass
    inv_log = [f"change_log: {len(recent)}/{len(change_log)} events in last 45d"]
    primary = None

    if recent:
        ts, entry = max(recent, key=lambda x: x[0])
        days_ago = (now - ts).days
        primary = {
            "kind": "change", "ts": entry.get("ts", ""), "days_ago": days_ago,
            "type": entry.get("type", ""), "op": entry.get("op", ""),
            "by": entry.get("by", ""), "fields": (entry.get("fields", "") or "")[:80],
        }

    # Investigation 2 — keyword set comparison (spend >= $15)
    kw_cur  = {k["text"] for k in periods_data.get("cur_month",  {}).get("keywords", []) if k.get("spend", 0) >= 15}
    kw_prev = {k["text"] for k in periods_data.get("prev_month", {}).get("keywords", []) if k.get("spend", 0) >= 15}
    disappeared = kw_prev - kw_cur
    appeared    = kw_cur  - kw_prev
    inv_log.append(f"keywords: {len(disappeared)} disappeared, {len(appeared)} new (spend≥$15)")
    if not primary:
        if disappeared:
            primary = {"kind": "kw_drop", "keywords": list(disappeared)[:3]}
        elif appeared:
            primary = {"kind": "kw_new",  "keywords": list(appeared)[:3]}

    # Investigation 3 — ad group shifts > $100
    ag_cur  = {a["name"]: a.get("spend", 0) for a in periods_data.get("cur_month",  {}).get("adgroups", [])}
    ag_prev = {a["name"]: a.get("spend", 0) for a in periods_data.get("prev_month", {}).get("adgroups", [])}
    ag_shifts = [(ag, ag_cur.get(ag, 0) - ag_prev.get(ag, 0)) for ag in set(ag_cur) | set(ag_prev)
                 if abs(ag_cur.get(ag, 0) - ag_prev.get(ag, 0)) > 100]
    inv_log.append(f"ad groups: {len(ag_shifts)} shifted >$100")
    if not primary and ag_shifts:
        biggest = max(ag_shifts, key=lambda x: abs(x[1]))
        primary = {"kind": "ag_shift", "name": biggest[0], "delta": biggest[1]}

    # Build WHY / REC
    if primary is None:
        why   = f"Investigated: {'; '.join(inv_log)}. No internal cause found."
        rec   = "Monitor 1 more week before action. Cannot confirm external cause without Auction Insights data."
        steps = ""
    elif primary["kind"] == "change":
        days     = primary["days_ago"]
        date_str = primary["ts"][:10]
        by       = primary.get("by", "unknown")
        res_type = primary.get("type", "resource")
        op       = primary.get("op", "change")
        fields   = primary.get("fields", "")
        why = f"Account change: {op} on {res_type} made {days}d ago ({date_str}) by {by}."
        if fields:
            why += f" Changed: {fields}"
        if days <= 14:
            resume = (now + datetime.timedelta(days=14 - days)).strftime("%b %d")
            rec = (f"WAIT — change is only {days}d old. Smart Bidding needs 14 days to restabilize. "
                   f"Re-evaluate on {resume}. Reverting now destroys the signal.")
        else:
            rec = (f"Change is {days}d old — past stabilization window. "
                   f"If metrics still degraded, revert the {op} on {res_type} from {date_str}.")
        steps = (f"1. ads.google.com → Change History → filter to {date_str}\n"
                 f"2. Find the '{op}' on '{res_type}'\n"
                 f"3. Note current value before reverting")
    elif primary["kind"] == "kw_drop":
        kws   = ", ".join(primary.get("keywords", []))
        why   = f"Keywords spending ≥$15 last month but not this month: {kws}. May have been paused or lost eligibility."
        rec   = "Check these keywords: if paused accidentally, re-enable. If budget-capped, cut lower performers to free budget."
        steps = f"1. ads.google.com → Keywords\n2. Search for: {kws}\n3. Check Status + First Page Bid estimate"
    elif primary["kind"] == "kw_new":
        kws   = ", ".join(primary.get("keywords", []))
        why   = f"New keywords spending this month not present last month: {kws}."
        rec   = "Check Quality Score on these keywords. QS < 5 → pause and rewrite ad copy to match keyword intent."
        steps = f"1. ads.google.com → Keywords\n2. Filter to: {kws}\n3. Check Quality Score + conversion rate"
    elif primary["kind"] == "ag_shift":
        name  = primary.get("name", "")
        delta = primary.get("delta", 0)
        dir_  = "increased" if delta > 0 else "decreased"
        why   = f"Ad group '{name}' spend {dir_} by ${abs(delta):.0f} MoM — largest shift in account."
        rec   = f"Review '{name}' ad group: check bid strategy changes and keyword competition shift."
        steps = (f"1. ads.google.com → Campaigns → Ad groups → '{name}'\n"
                 f"2. Check bid strategy + keyword bids\n"
                 f"3. Compare CTR and Quality Scores vs last month")
    else:
        why   = "No clear cause identified."
        rec   = "Monitor 1 more week."
        steps = ""

    return {"obs": obs, "why": why, "rec": rec, "steps": steps, "investigation_log": inv_log}


def write_to_ads_sheet(today, periods_data, change_log, sheet_id, sheets_token_json):
    """Append one row of weekly snapshot data to the OPC Ads Data sheet."""
    import urllib.parse, urllib.request
    td = json.loads(sheets_token_json)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    token = resp["access_token"]

    c = periods_data.get("cur_month", {}).get("campaign", {})
    p = periods_data.get("prev_month", {}).get("campaign", {})
    cpl = c.get("spend", 0) / max(int(c.get("calls", 0)), 1)

    row = [
        today,
        round(c.get("spend", 0), 2),
        int(c.get("calls", 0)),
        int(c.get("clicks", 0)),
        round(c.get("ctr", 0), 2),
        round(c.get("avg_cpc", 0), 2),
        round(cpl, 0),
        int(c.get("conversions", 0)),
        round(c.get("is_pct", 0) or 0, 1),
        round(p.get("spend", 0), 2),
        int(p.get("calls", 0)),
        len(change_log),
    ]

    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
           f"/values/Weekly%20Snapshot:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    body = json.dumps({"values": [row]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        urllib.request.urlopen(req, timeout=15)
        print(f"  Ads sheet updated: {sheet_id}")
    except Exception as e:
        print(f"  Ads sheet write failed: {e}")


def build_email_body(today, periods_data, calls, insight=None):
    c30 = periods_data["30d"]["campaign"]
    spend  = c30.get("spend", 0)
    clicks = c30.get("clicks", 0)
    ctr    = c30.get("ctr", 0)
    calls_count = int(c30.get("calls", 0))
    conv   = int(c30.get("conversions", 0))
    cpc    = c30.get("avg_cpc", 0)
    cpl    = spend / max(calls_count, 1)

    received = sum(1 for c in calls if c["status"] == "RECEIVED")
    missed   = sum(1 for c in calls if c["status"] == "MISSED")

    body = f"""OPC Google Ads — Weekly Dashboard ({today})

Dashboard: https://priihigashi.github.io/oak-park-ai-hub/dashboard/

CAMPAIGN — LAST 30 DAYS
  Spend:       ${spend:.2f}
  Clicks:      {clicks}
  CTR:         {ctr:.2f}%
  Calls:       {calls_count}
  Conversions: {conv}
  Avg CPC:     ${cpc:.2f}
  Cost/Call:   ${cpl:.0f}

ALL-TIME CALL LOG ({len(calls)} total)
  Received: {received}
  Missed:   {missed}

REQUIRES ATTENTION
"""
    # Inline warning generation for email
    kws  = periods_data["30d"]["keywords"]
    ags  = periods_data["30d"]["adgroups"]
    zero_spend = sum(k["spend"] for k in kws if k["conv"] == 0)

    if calls_count < 5:
        body += f"🔴 Only {calls_count} calls in 30 days — very low lead volume for ${spend:.0f} spent.\n"
    elif cpl > 300:
        body += f"🔴 Cost per call ${cpl:.0f} — target is under $150. Fix conversion tracking.\n"

    if zero_spend > 200:
        top3 = [k for k in kws if k["conv"] == 0][:3]
        names = ", ".join(f"{k['text']} (${k['spend']:.0f})" for k in top3)
        body += f"🔴 ${zero_spend:.0f} spent on 0-conversion keywords: {names}\n"

    stucco = next((a for a in ags if "STUCCO" in a["name"].upper()), None)
    if stucco and stucco["spend"] == 0:
        body += "🟡 STUCCO ad group: $0 spend — ads not showing.\n"

    if calls_count > conv * 2 and conv < calls_count:
        body += f"🟡 {calls_count} calls but only {conv} conversion tracked — check GA4 link.\n"

    body += "🟡 Website has NO tracking code (GA4 not installed on oakpark-construction.com)\n"

    # MoM AI analysis — evidence-driven
    if insight:
        body += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MONTH-OVER-MONTH ANALYSIS (AI)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT CHANGED
  {insight['obs']}

WHY
  {insight['why']}

RECOMMENDATION
  {insight['rec']}
"""
        if insight.get("steps"):
            body += f"\nSTEPS\n"
            for line in insight["steps"].split("\n"):
                body += f"  {line}\n"

    return body


def main():
    customer_id  = "8945889168"
    dev_token    = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
    client_id    = os.environ["GOOGLE_ADS_CLIENT_ID"]
    client_secret= os.environ["GOOGLE_ADS_CLIENT_SECRET"]
    refresh_token= os.environ["GOOGLE_ADS_REFRESH_TOKEN"]
    gmail_pw     = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")

    config = {
        "developer_token": dev_token,
        "client_id":       client_id,
        "client_secret":   client_secret,
        "refresh_token":   refresh_token,
        "use_proto_plus":  True,
    }

    print("Pulling Google Ads data (all periods)...")
    periods_data, calls, config_data, change_log, dow_data, recent_daily = get_all_data(customer_id, config)

    today = datetime.date.today().isoformat()
    data  = {
        "updated":      today,
        "periods":      periods_data,
        "calls":        calls,
        "config":       config_data,
        "changes":      change_log,
        "dow":          dow_data,
        "recent_daily": recent_daily,
    }

    dashboard_dir = Path(__file__).parent.parent / "docs" / "dashboard"
    for fname in ("index.html", "dark.html"):
        path = dashboard_dir / fname
        if path.exists():
            inject_data(path, data)
            print(f"Dashboard updated: {path}")

    # Server-side evidence-driven MoM analysis
    insight = build_mom_insight(periods_data, change_log)
    if insight:
        print(f"  MoM insight: {insight['obs']}")

    if gmail_pw:
        body = build_email_body(today, periods_data, calls, insight)
        send_email(f"OPC Ads Weekly — {today}", body, gmail_pw)
        print("Email sent.")

    # Write weekly snapshot to Ads Data spreadsheet (if SHEETS_TOKEN available)
    sheets_token = os.environ.get("SHEETS_TOKEN", "")
    ads_sheet_id = os.environ.get("OPC_ADS_SHEET_ID", "")
    if sheets_token and ads_sheet_id:
        write_to_ads_sheet(today, periods_data, change_log, ads_sheet_id, sheets_token)


if __name__ == "__main__":
    main()
