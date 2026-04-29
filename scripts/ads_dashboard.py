"""
ads_dashboard.py — Pull Google Ads data, regenerate docs/dashboard/index.html
Run by ads_pulse.yml every Monday 8 AM ET.
"""
import json, os, re, sys, smtplib, datetime
from email.message import EmailMessage
from pathlib import Path

def get_ads_data(customer_id, config):
    from google.ads.googleads.client import GoogleAdsClient
    client = GoogleAdsClient.load_from_dict(config)
    ga = client.get_service("GoogleAdsService")

    # Campaign overview
    r1 = ga.search(customer_id=customer_id, query="""
        SELECT campaign.name, metrics.clicks, metrics.impressions, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros, metrics.conversions, metrics.phone_calls
        FROM campaign
        WHERE segments.date DURING LAST_30_DAYS AND campaign.id = 23314409466
    """)
    campaign = {}
    for row in r1:
        m = row.metrics
        campaign = {
            "clicks": m.clicks, "impressions": m.impressions,
            "ctr": round(m.ctr * 100, 2),
            "avg_cpc": round(m.average_cpc / 1e6, 2),
            "spend": round(m.cost_micros / 1e6, 2),
            "conversions": m.conversions, "calls": m.phone_calls
        }

    # Ad groups
    r2 = ga.search(customer_id=customer_id, query="""
        SELECT ad_group.name, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM ad_group
        WHERE segments.date DURING LAST_30_DAYS AND campaign.id = 23314409466
        ORDER BY metrics.cost_micros DESC
    """)
    adgroups = [{"name": row.ad_group.name, "spend": round(row.metrics.cost_micros/1e6,2),
                 "clicks": row.metrics.clicks, "conv": row.metrics.conversions} for row in r2]

    # Top keywords
    r3 = ga.search(customer_id=customer_id, query="""
        SELECT ad_group.name, ad_group_criterion.keyword.text,
               metrics.clicks, metrics.impressions, metrics.ctr,
               metrics.cost_micros, metrics.conversions, metrics.average_cpc
        FROM keyword_view
        WHERE segments.date DURING LAST_30_DAYS AND campaign.id = 23314409466
          AND metrics.cost_micros > 0
        ORDER BY metrics.cost_micros DESC LIMIT 10
    """)
    keywords = [{"text": row.ad_group_criterion.keyword.text, "ag": row.ad_group.name,
                 "spend": round(row.metrics.cost_micros/1e6,2), "clicks": row.metrics.clicks,
                 "ctr": round(row.metrics.ctr*100,2), "conv": row.metrics.conversions} for row in r3]

    return campaign, adgroups, keywords


def build_warnings(campaign, adgroups, keywords):
    warnings = []
    spend = campaign.get("spend", 0)
    calls = campaign.get("calls", 0)
    conv  = campaign.get("conversions", 0)
    cpl   = round(spend / max(calls, 1), 0)

    if calls < 5:
        warnings.append({"level":"red","icon":"🔴",
            "text": f"<strong>Only {int(calls)} calls in 30 days.</strong> Very low lead volume for ${spend:.0f} spent.",
            "action": "Check location targeting, ad scheduling, and bid strategy"})
    elif cpl > 300:
        warnings.append({"level":"red","icon":"🔴",
            "text": f"<strong>${spend:.0f} spent → {int(calls)} calls. Cost per call = ${cpl:.0f}.</strong> Target should be under $150.",
            "action": "Fix conversion tracking first — Google can't optimise without it"})

    zero_conv_spend = sum(k["spend"] for k in keywords if k["conv"] == 0)
    top_zero = [k for k in keywords if k["conv"] == 0][:3]
    if zero_conv_spend > 200:
        names = ", ".join(f"{k['text']} (${k['spend']:.0f})" for k in top_zero)
        warnings.append({"level":"red","icon":"🔴",
            "text": f"<strong>${zero_conv_spend:.0f} spent on 0-conversion keywords.</strong> Top offenders: {names}.",
            "action": "Consider pausing these or adding as negatives"})

    stucco = next((a for a in adgroups if "STUCCO" in a["name"].upper()), None)
    if stucco and stucco["spend"] == 0:
        warnings.append({"level":"yellow","icon":"🟡",
            "text": "<strong>STUCCO ad group: $0 spend, 0 clicks.</strong> Ads not showing — below minimum bid or Quality Score issue.",
            "action": "Check bid vs. first-page estimate in Google Ads UI"})

    if calls > conv * 2 and conv < calls:
        warnings.append({"level":"yellow","icon":"🟡",
            "text": f"<strong>{int(calls)} calls tracked but only {int(conv)} conversion logged.</strong> {int(calls - conv)} real leads may be invisible to Google.",
            "action": "Link GA4 to Google Ads + verify Call conversion action is set as Primary"})

    return warnings


def inject_data(html_path, data):
    html = Path(html_path).read_text()
    new_block = "const DATA = " + json.dumps(data, indent=2) + ";"
    html = re.sub(r"const DATA = \{.*?\};", new_block, html, flags=re.DOTALL)
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


def main():
    customer_id = "8945889168"
    dev_token    = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
    client_id    = os.environ["GOOGLE_ADS_CLIENT_ID"]
    client_secret= os.environ["GOOGLE_ADS_CLIENT_SECRET"]
    refresh_token= os.environ["GOOGLE_ADS_REFRESH_TOKEN"]
    gmail_pw     = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD","")

    config = {
        "developer_token": dev_token,
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "use_proto_plus": True
    }

    print("Pulling Google Ads data...")
    campaign, adgroups, keywords = get_ads_data(customer_id, config)

    warnings = build_warnings(campaign, adgroups, keywords)
    today = datetime.date.today().isoformat()

    data = {
        "updated": today,
        "campaign": campaign,
        "adgroups": adgroups,
        "keywords": keywords,
        "warnings": warnings
    }

    html_path = Path(__file__).parent.parent / "docs" / "dashboard" / "index.html"
    inject_data(html_path, data)
    print(f"Dashboard updated: {html_path}")

    # Email summary
    if gmail_pw:
        warn_lines = "\n".join(
            f"{'🔴' if w['level']=='red' else '🟡'} {w['text'].replace('<strong>','').replace('</strong>','')}"
            for w in warnings
        )
        body = f"""OPC Google Ads — Weekly Dashboard ({today})

Dashboard: https://priihigashi.github.io/oak-park-ai-hub/dashboard/

CAMPAIGN (Last 30 days)
  Spend:       ${campaign.get('spend',0):.2f}
  Clicks:      {campaign.get('clicks',0)}
  CTR:         {campaign.get('ctr',0):.2f}%
  Calls:       {int(campaign.get('calls',0))}
  Conversions: {int(campaign.get('conversions',0))}
  Avg CPC:     ${campaign.get('avg_cpc',0):.2f}

REQUIRES ATTENTION
{warn_lines}
"""
        send_email(f"OPC Ads Weekly Report — {today}", body, gmail_pw)
        print("Email sent.")


if __name__ == "__main__":
    main()
