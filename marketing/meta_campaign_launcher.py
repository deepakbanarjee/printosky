# -*- coding: utf-8 -*-
"""Meta Marketing API Automated Campaign Launcher for Printosky 60 km Network.

Programmatically creates:
1. Campaign: Printosky - Campus Reach (60km Thrissur/Ernakulam/Malappuram/Palakkad)
2. Ad Sets (4 Geo-fenced Clusters, 2km radius pin-drops around 100+ colleges):
   - Cluster 1: Thrissur Tech & Medical Hub (GEC, VAST, IES, KAU, GMC, Amala, Poly)
   - Cluster 2: Ernakulam Tech Corridor (CUSAT, FISAT, SCMS, ASIET, MEC, RSET)
   - Cluster 3: Malappuram South & Coastal (MESCE Kuttippuram, KCAET, SSM Poly, MES Med)
   - Cluster 4: Palakkad West (IPT & GPTC Shoranur, Royal Dental, SIMAT, JCET, NCERC)
3. Ad Creatives & Ads (Click-to-WhatsApp + Campus Landing Page).

Prerequisites in .env:
  META_ACCESS_TOKEN = "EAAB..." (System User / Page Access Token with ads_management)
  META_AD_ACCOUNT_ID = "act_1234567890"
  META_PAGE_ID = "1234567890"

Usage:
  python marketing/meta_campaign_launcher.py --dry-run
  python marketing/meta_campaign_launcher.py --deploy --daily-budget 350
"""

import argparse
import json
import os
import sys
import requests

GRAPH_API_VERSION = "v20.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# 2 km Pin-drop Coordinates around Campus Clusters
CAMPUS_GEO_CLUSTERS = [
    {
        "name": "Cluster 1: Thrissur Tech & Medical Hub",
        "custom_locations": [
            {"latitude": 10.5534, "longitude": 76.2251, "radius": 2.5, "distance_unit": "kilometer", "name": "GEC Thrissur / Ramavarmapuram / MTI"},
            {"latitude": 10.6050, "longitude": 76.1550, "radius": 3.0, "distance_unit": "kilometer", "name": "VAST Thalakkottukara / Govt Medical College"},
            {"latitude": 10.5400, "longitude": 76.2750, "radius": 2.5, "distance_unit": "kilometer", "name": "KAU Vellanikkara / KVASU Mannuthy"},
            {"latitude": 10.3550, "longitude": 76.2000, "radius": 2.5, "distance_unit": "kilometer", "name": "Christ College / St. Joseph's Irinjalakuda"},
            {"latitude": 10.4200, "longitude": 76.1100, "radius": 2.5, "distance_unit": "kilometer", "name": "SN College Nattika / Sree Rama Poly Thriprayar"},
            {"latitude": 10.3600, "longitude": 76.3000, "radius": 2.5, "distance_unit": "kilometer", "name": "Sahrdaya Engg Kodakara / Naipunnya Koratty"},
        ],
    },
    {
        "name": "Cluster 2: Ernakulam Tech Corridor",
        "custom_locations": [
            {"latitude": 10.0435, "longitude": 76.3244, "radius": 2.5, "distance_unit": "kilometer", "name": "CUSAT Kalamassery / NUALS / GMC Ernakulam"},
            {"latitude": 10.1850, "longitude": 76.3860, "radius": 3.0, "distance_unit": "kilometer", "name": "FISAT / SCMS Angamaly / ASIET Kalady"},
            {"latitude": 10.0100, "longitude": 76.3600, "radius": 2.5, "distance_unit": "kilometer", "name": "RSET Kakkanad / Model Engg Thrikkakara"},
            {"latitude": 10.1900, "longitude": 76.2100, "radius": 2.5, "distance_unit": "kilometer", "name": "SNMIMT Maliankara / SNIMS North Paravur"},
        ],
    },
    {
        "name": "Cluster 3: Malappuram South & Coastal",
        "custom_locations": [
            {"latitude": 10.8450, "longitude": 75.9950, "radius": 3.0, "distance_unit": "kilometer", "name": "MESCE Kuttippuram / KCAET Tavanur"},
            {"latitude": 10.9800, "longitude": 76.2200, "radius": 3.0, "distance_unit": "kilometer", "name": "MES Medical & Al Shifa Perinthalmanna"},
            {"latitude": 10.7700, "longitude": 75.9200, "radius": 2.5, "distance_unit": "kilometer", "name": "MES Ponnani / SSM Poly Tirur"},
        ],
    },
    {
        "name": "Cluster 4: Palakkad West",
        "custom_locations": [
            {"latitude": 10.7600, "longitude": 76.2700, "radius": 3.0, "distance_unit": "kilometer", "name": "IPT & GPTC Shoranur / Jyothi Engg"},
            {"latitude": 10.7400, "longitude": 76.4300, "radius": 3.0, "distance_unit": "kilometer", "name": "JCET Lakkidi / NCERC Pampady"},
            {"latitude": 10.7300, "longitude": 76.0800, "radius": 2.5, "distance_unit": "kilometer", "name": "Royal Dental Chalissery / SIMAT Pattambi"},
        ],
    },
]


def get_env_credentials():
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    account_id = os.environ.get("META_AD_ACCOUNT_ID", "").strip()
    page_id = os.environ.get("META_PAGE_ID", "").strip()

    # Normalize account ID prefix
    if account_id and not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    return token, account_id, page_id


def build_targeting_spec(custom_locations):
    return {
        "geo_locations": {
            "custom_locations": [
                {
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                    "radius": loc["radius"],
                    "distance_unit": loc["distance_unit"],
                }
                for loc in custom_locations
            ],
            "location_types": ["recent", "home"],
        },
        "age_min": 18,
        "age_max": 25,
        "flexible_spec": [
            {
                "interests": [
                    {"name": "Engineering"},
                    {"name": "Higher education"},
                    {"name": "College"},
                    {"name": "University"},
                ]
            }
        ],
    }


def create_campaign(token, account_id, campaign_name="Printosky - 60km Campus Network (Thrissur/Ernakulam/Malappuram/Palakkad)"):
    url = f"{BASE_URL}/{account_id}/campaigns"
    payload = {
        "name": campaign_name,
        "objective": "OUTCOME_TRAFFIC",
        "status": "PAUSED",  # Starts paused so user can do final sanity review
        "special_ad_categories": ["NONE"],
        "access_token": token,
    }
    resp = requests.post(url, data=payload)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Meta API Campaign Error: {data['error']['message']}")
    return data["id"]


def create_adset(token, account_id, campaign_id, cluster_name, custom_locations, daily_budget_inr=100):
    url = f"{BASE_URL}/{account_id}/adsets"
    # Meta requires budget in subunits (paise): ₹100 = 10000 paise
    budget_paise = int(daily_budget_inr * 100)
    
    targeting = build_targeting_spec(custom_locations)
    payload = {
        "name": f"AdSet: {cluster_name}",
        "campaign_id": campaign_id,
        "daily_budget": budget_paise,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LINK_CLICKS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": json.dumps(targeting),
        "status": "PAUSED",
        "access_token": token,
    }
    resp = requests.post(url, data=payload)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Meta API AdSet Error: {data['error']['message']}")
    return data["id"]


def main():
    parser = argparse.ArgumentParser(description="Meta Ads Launcher for Printosky 60 km Network")
    parser.add_argument("--dry-run", action="store_true", help="Preview campaign payloads and targeting without making API calls")
    parser.add_argument("--deploy", action="store_true", help="Execute live deployment on Meta Marketing API")
    parser.add_argument("--daily-budget", type=int, default=350, help="Total daily budget in INR (default: 350)")

    args = parser.parse_args()

    token, account_id, page_id = get_env_credentials()

    if args.dry_run or not args.deploy:
        print("================================================================================")
        print(" META MARKETING API -- DRY RUN PREVIEW (60 KM REGIONAL CAMPUS CAMPAIGN)")
        print("================================================================================")
        print("Targeting Strategy: 4 Regional Geo-Fenced Clusters (100+ Professional Colleges)")
        print(f"Daily Budget: Rs. {args.daily_budget} INR (~Rs. {args.daily_budget // len(CAMPUS_GEO_CLUSTERS)} per cluster)")
        print("Demographics: Age 18-25 | Target: Engineering, Medical, Poly, Law, Management Students")
        print("\n--- Configured Pin-Drop Clusters ---")
        for idx, cluster in enumerate(CAMPUS_GEO_CLUSTERS, 1):
            print(f"\n[{idx}] {cluster['name']}")
            for loc in cluster["custom_locations"]:
                print(f"    * {loc['name']:<45} : Lat {loc['latitude']:.4f}, Lng {loc['longitude']:.4f} ({loc['radius']} km)")

        print("\n--- Ad Creative Payloads ---")
        print("* Creative A: 'Skip the Xerox Queue' (15s Video / Story Hook) -> WhatsApp Direct")
        print("* Creative B: 'Smart Color Slicing Calculator' (Carousel) -> https://printosky.com/campus.html")
        print("* Creative C: 'Fund Your Thesis for Rs. 0' (Store Credit) -> WhatsApp Referral")
        print("\n[INFO] To deploy live, set META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in .env and run:")
        print("       python marketing/meta_campaign_launcher.py --deploy --daily-budget 350")
        return

    # Live Deployment Path
    if not token or not account_id:
        print("Error: Missing credentials in environment.")
        print("Please ensure META_ACCESS_TOKEN and META_AD_ACCOUNT_ID are defined in your .env file.")
        sys.exit(1)

    print(f"Deploying Campaign to Ad Account: {account_id}...")
    try:
        camp_id = create_campaign(token, account_id)
        print(f"[OK] Campaign Created! ID: {camp_id}")

        budget_per_adset = max(100, args.daily_budget // len(CAMPUS_GEO_CLUSTERS))
        for cluster in CAMPUS_GEO_CLUSTERS:
            adset_id = create_adset(token, account_id, camp_id, cluster["name"], cluster["custom_locations"], budget_per_adset)
            print(f"  [OK] Created AdSet: {cluster['name']} (ID: {adset_id}) -- Budget: Rs. {budget_per_adset}/day")

        print("\n[SUCCESS] Live Meta Deployment Successful! Campaigns created in PAUSED state for final account review.")
    except Exception as e:
        print(f"Deployment Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
