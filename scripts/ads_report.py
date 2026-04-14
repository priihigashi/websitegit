"""Read-only Google Ads report for Oak Park Construction.

This script uses the Google Ads API REST endpoint directly so the workflow does
not need the google-ads Python package. It never calls mutate endpoints.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CUSTOMER_ID = "8945889168"
DEFAULT_API_VERSION = "v22"
OUTPUT_DIR = Path(os.environ.get("ADS_REPORT_OUTPUT_DIR", "artifacts/ads-report"))


@dataclass(frozen=True)
class AdsConfig:
    developer_token: str
    login_customer_id: str
    customer_id: str
    api_version: str
    access_token: str


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _load_token_payload() -> dict[str, Any]:
    raw = _required_env("PRI_OP_ADS_TOKEN")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("PRI_OP_ADS_TOKEN must be OAuth token JSON") from exc


def _refresh_access_token(token_payload: dict[str, Any]) -> str:
    refresh_token = token_payload.get("refresh_token")
    client_id = token_payload.get("client_id")
    client_secret = token_payload.get("client_secret")
    token_uri = token_payload.get("token_uri") or "https://oauth2.googleapis.com/token"

    missing = [
        name
        for name, value in (
            ("refresh_token", refresh_token),
            ("client_id", client_id),
            ("client_secret", client_secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"PRI_OP_ADS_TOKEN missing field(s): {', '.join(missing)}")

    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        token_uri,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("OAuth refresh response did not include access_token")
    return access_token


def load_config() -> AdsConfig:
    token_payload = _load_token_payload()
    return AdsConfig(
        developer_token=_required_env("GOOGLE_ADS_DEVELOPER_TOKEN"),
        login_customer_id=os.environ.get("GOOGLE_ADS_MCC_ID", "5870713494").replace("-", ""),
        customer_id=os.environ.get("GOOGLE_ADS_CUSTOMER_ID", DEFAULT_CUSTOMER_ID).replace("-", ""),
        api_version=os.environ.get("GOOGLE_ADS_API_VERSION", DEFAULT_API_VERSION).strip(),
        access_token=_refresh_access_token(token_payload),
    )


def ads_search(config: AdsConfig, query: str) -> list[dict[str, Any]]:
    url = (
        f"https://googleads.googleapis.com/{config.api_version}/customers/"
        f"{config.customer_id}/googleAds:searchStream"
    )
    body = json.dumps({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {config.access_token}",
            "developer-token": config.developer_token,
            "login-customer-id": config.login_customer_id,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            chunks = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Google Ads API request failed with HTTP {exc.code}: {detail}"
        ) from exc

    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        rows.extend(chunk.get("results", []))
    return rows


def micros_to_dollars(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return int(value) / 1_000_000


def number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def pct(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def campaign_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in raw_rows:
        campaign = row.get("campaign", {})
        metrics = row.get("metrics", {})
        clicks = number(metrics.get("clicks"))
        cost = micros_to_dollars(metrics.get("costMicros"))
        conversions = number(metrics.get("conversions"))
        rows.append(
            {
                "campaign_id": campaign.get("id"),
                "campaign_name": campaign.get("name"),
                "status": campaign.get("status"),
                "impressions": int(number(metrics.get("impressions"))),
                "clicks": int(clicks),
                "cost": round(cost, 2),
                "conversions": conversions,
                "ctr": number(metrics.get("ctr")),
                "average_cpc": round(micros_to_dollars(metrics.get("averageCpc")), 2),
                "cost_per_conversion": round(cost / conversions, 2)
                if conversions
                else None,
                "search_impression_share": metrics.get("searchImpressionShare"),
            }
        )
    return rows


def ad_group_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in raw_rows:
        campaign = row.get("campaign", {})
        ad_group = row.get("adGroup", {})
        metrics = row.get("metrics", {})
        clicks = number(metrics.get("clicks"))
        cost = micros_to_dollars(metrics.get("costMicros"))
        conversions = number(metrics.get("conversions"))
        rows.append(
            {
                "campaign_name": campaign.get("name"),
                "ad_group_id": ad_group.get("id"),
                "ad_group_name": ad_group.get("name"),
                "status": ad_group.get("status"),
                "impressions": int(number(metrics.get("impressions"))),
                "clicks": int(clicks),
                "cost": round(cost, 2),
                "conversions": conversions,
                "ctr": number(metrics.get("ctr")),
                "average_cpc": round(micros_to_dollars(metrics.get("averageCpc")), 2),
                "cost_per_conversion": round(cost / conversions, 2)
                if conversions
                else None,
            }
        )
    return rows


def search_term_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in raw_rows:
        campaign = row.get("campaign", {})
        ad_group = row.get("adGroup", {})
        term = row.get("searchTermView", {})
        metrics = row.get("metrics", {})
        cost = micros_to_dollars(metrics.get("costMicros"))
        conversions = number(metrics.get("conversions"))
        rows.append(
            {
                "campaign_name": campaign.get("name"),
                "ad_group_name": ad_group.get("name"),
                "search_term": term.get("searchTerm"),
                "impressions": int(number(metrics.get("impressions"))),
                "clicks": int(number(metrics.get("clicks"))),
                "cost": round(cost, 2),
                "conversions": conversions,
                "ctr": number(metrics.get("ctr")),
                "average_cpc": round(micros_to_dollars(metrics.get("averageCpc")), 2),
            }
        )
    return rows


def summarize(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    total_cost = sum(row["cost"] for row in campaigns)
    total_clicks = sum(row["clicks"] for row in campaigns)
    total_impressions = sum(row["impressions"] for row in campaigns)
    total_conversions = sum(row["conversions"] for row in campaigns)
    return {
        "impressions": total_impressions,
        "clicks": total_clicks,
        "cost": round(total_cost, 2),
        "conversions": total_conversions,
        "ctr": total_clicks / total_impressions if total_impressions else 0.0,
        "average_cpc": round(total_cost / total_clicks, 2) if total_clicks else 0.0,
        "cost_per_conversion": round(total_cost / total_conversions, 2)
        if total_conversions
        else None,
    }


def find_waste(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("cost", 0) >= 10 and row.get("clicks", 0) >= 3 and not row.get("conversions")
    ][:10]


def markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 10) -> str:
    if not rows:
        return "_No rows returned._"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for row in rows[:limit]:
        cells = []
        for col in columns:
            value = row.get(col)
            if isinstance(value, float) and col in {"ctr", "search_impression_share"}:
                value = pct(value)
            elif value is None:
                value = "n/a"
            cells.append(str(value).replace("|", "/"))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider, *body])


def build_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    campaigns = payload["campaigns"]
    ad_groups = payload["ad_groups"]
    search_terms = payload["search_terms"]
    waste_campaigns = find_waste(campaigns)
    waste_terms = find_waste(search_terms)

    lines = [
        "# Oak Park Construction Google Ads Report",
        "",
        f"Generated: {payload['generated_at']}",
        f"Customer ID: {payload['customer_id']}",
        f"Date range: {payload['date_range']}",
        "",
        "## Account Snapshot",
        "",
        f"- Impressions: {summary['impressions']:,}",
        f"- Clicks: {summary['clicks']:,}",
        f"- Cost: ${summary['cost']:,.2f}",
        f"- Conversions: {summary['conversions']:.2f}",
        f"- CTR: {pct(summary['ctr'])}",
        f"- Avg CPC: ${summary['average_cpc']:,.2f}",
        f"- Cost per conversion: "
        f"{'$' + format(summary['cost_per_conversion'], ',.2f') if summary['cost_per_conversion'] else 'n/a'}",
        "",
        "## Top Campaigns By Spend",
        "",
        markdown_table(
            campaigns,
            [
                "campaign_name",
                "status",
                "impressions",
                "clicks",
                "cost",
                "conversions",
                "ctr",
                "average_cpc",
                "cost_per_conversion",
            ],
        ),
        "",
        "## Top Ad Groups By Spend",
        "",
        markdown_table(
            ad_groups,
            [
                "campaign_name",
                "ad_group_name",
                "status",
                "clicks",
                "cost",
                "conversions",
                "average_cpc",
                "cost_per_conversion",
            ],
        ),
        "",
        "## Search Terms By Spend",
        "",
        markdown_table(
            search_terms,
            [
                "campaign_name",
                "ad_group_name",
                "search_term",
                "clicks",
                "cost",
                "conversions",
                "average_cpc",
            ],
        ),
        "",
        "## Watch List",
        "",
    ]

    if waste_campaigns:
        lines.extend(
            [
                "Campaigns with spend, clicks, and zero conversions:",
                "",
                markdown_table(
                    waste_campaigns,
                    ["campaign_name", "status", "clicks", "cost", "conversions", "average_cpc"],
                ),
                "",
            ]
        )
    else:
        lines.extend(["No campaign-level zero-conversion spend flags hit the threshold.", ""])

    if waste_terms:
        lines.extend(
            [
                "Search terms with spend, clicks, and zero conversions:",
                "",
                markdown_table(
                    waste_terms,
                    ["campaign_name", "ad_group_name", "search_term", "clicks", "cost", "average_cpc"],
                ),
                "",
            ]
        )
    else:
        lines.extend(["No search-term zero-conversion spend flags hit the threshold.", ""])

    lines.extend(
        [
            "## Guardrails",
            "",
            "- Read-only report only: no campaign mutations were called.",
            "- Claude review required before campaign controls, conversion setup, or dashboard rollout.",
            "- If this workflow fails with access/approval errors, keep waiting for the real Basic Access approval email.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    config = load_config()
    date_range = os.environ.get("GOOGLE_ADS_DATE_RANGE", "LAST_30_DAYS").strip()

    campaign_query = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc,
          metrics.search_impression_share
        FROM campaign
        WHERE segments.date DURING {date_range}
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """
    ad_group_query = f"""
        SELECT
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc
        FROM ad_group
        WHERE segments.date DURING {date_range}
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """
    search_term_query = f"""
        SELECT
          campaign.name,
          ad_group.name,
          search_term_view.search_term,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc
        FROM search_term_view
        WHERE segments.date DURING {date_range}
          AND metrics.impressions > 0
        ORDER BY metrics.cost_micros DESC
        LIMIT 100
    """

    campaigns = campaign_rows(ads_search(config, campaign_query))
    ad_groups = ad_group_rows(ads_search(config, ad_group_query))
    search_terms = search_term_rows(ads_search(config, search_term_query))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "customer_id": config.customer_id,
        "login_customer_id": config.login_customer_id,
        "date_range": date_range,
        "summary": summarize(campaigns),
        "campaigns": campaigns,
        "ad_groups": ad_groups,
        "search_terms": search_terms,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = OUTPUT_DIR / f"ads_report_{today}.json"
    md_path = OUTPUT_DIR / f"ads_report_{today}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_report(payload), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote Markdown: {md_path}")
    print(
        "Snapshot: "
        f"{payload['summary']['clicks']} clicks, "
        f"${payload['summary']['cost']:.2f} spend, "
        f"{payload['summary']['conversions']:.2f} conversions"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
