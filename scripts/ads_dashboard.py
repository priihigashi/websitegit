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
               metrics.average_cpc, metrics.cost_micros, metrics.conversions, metrics.phone_calls
        FROM campaign
        WHERE {date_filter} AND campaign.id = {CAMPAIGN_ID}
    """)
    campaign = {}
    for row in r1:
        m = row.metrics
        campaign = {
            "clicks":      m.clicks,
            "impressions": m.impressions,
            "ctr":         round(m.ctr * 100, 2),
            "avg_cpc":     round(m.average_cpc / 1e6, 2),
            "spend":       round(m.cost_micros / 1e6, 2),
            "conversions": m.conversions,
            "calls":       m.phone_calls,
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
              AND change_event.campaign = 'customers/{customer_id}/campaigns/{CAMPAIGN_ID}'
            ORDER BY change_event.change_date_time DESC
            LIMIT 100
        """)
        for row in r:
            ce = row.change_event
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

    print("  Pulling config (budget, bid strategy, max CPC)...")
    config_data = get_config_data(ga, customer_id)

    print("  Pulling change log (last 30d)...")
    change_log = get_change_log(ga, customer_id)

    return periods_data, calls, config_data, change_log


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


def build_email_body(today, periods_data, calls):
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
    periods_data, calls, config_data, change_log = get_all_data(customer_id, config)

    today = datetime.date.today().isoformat()
    data  = {
        "updated": today,
        "periods": periods_data,
        "calls":   calls,
        "config":  config_data,
        "changes": change_log,
    }

    dashboard_dir = Path(__file__).parent.parent / "docs" / "dashboard"
    for fname in ("index.html", "dark.html"):
        path = dashboard_dir / fname
        if path.exists():
            inject_data(path, data)
            print(f"Dashboard updated: {path}")

    if gmail_pw:
        body = build_email_body(today, periods_data, calls)
        send_email(f"OPC Ads Weekly Report — {today}", body, gmail_pw)
        print("Email sent.")


if __name__ == "__main__":
    main()
