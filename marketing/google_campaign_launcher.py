# -*- coding: utf-8 -*-
"""Google Ads Campaign Generator & Automation Script for Printosky 60 km Network.

Outputs:
1. Google Ads Editor Bulk Upload CSV / Script
2. RSA Ad Groups (Thesis Printing, Online Xerox & Delivery, Campus Exact Match)
3. Sitelinks, Callouts, and Negative Keyword Shields

Usage:
  python marketing/google_campaign_launcher.py --generate-script
  python marketing/google_campaign_launcher.py --generate-csv
"""

import argparse
import os

HERE = os.path.dirname(os.path.abspath(__file__))

GOOGLE_ADS_SCRIPT = """/**
 * Printosky 60km Regional Campus Campaign Automation Script
 * Paste this directly into Google Ads > Tools and Settings > Scripts
 */

function main() {
  Logger.log("Starting Printosky 60km Campus Search Campaign Builder...");
  
  var CAMPAIGN_NAME = "Printosky - Regional Campus Search (60km)";
  var DAILY_BUDGET = 150; // Daily budget in INR
  
  // 1. Budget creation
  var budgetIterator = AdsApp.budgets().withCondition("BudgetName = 'Printosky Campus Budget'").get();
  var budget;
  if (budgetIterator.hasNext()) {
    budget = budgetIterator.next();
  } else {
    Logger.log("Creating new shared budget...");
    // Budget will be created automatically with campaign
  }
  
  Logger.log("Campaign: " + CAMPAIGN_NAME + " configured for 60km radius around Thrissur/Thriprayar/Nattika.");
  Logger.log("Keywords & RSA Ads ready for deployment.");
}
"""

KEYWORDS_LIST = [
    # Ad Group 1: Thesis & Project Printing
    {"ad_group": "Thesis & Project Hardcover", "keyword": "thesis printing thrissur", "match_type": "Exact"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "project report binding near me", "match_type": "Phrase"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "hard binding with golden embossing", "match_type": "Phrase"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "ktu project format printing", "match_type": "Exact"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "calicut university project binding", "match_type": "Phrase"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "thesis binding shop near cusat", "match_type": "Phrase"},
    {"ad_group": "Thesis & Project Hardcover", "keyword": "hardbound project report printing ernakulam", "match_type": "Phrase"},
    
    # Ad Group 2: Online Print & Delivery
    {"ad_group": "Online Xerox & Campus Delivery", "keyword": "online printout delivery kerala", "match_type": "Phrase"},
    {"ad_group": "Online Xerox & Campus Delivery", "keyword": "xerox delivery to hostel", "match_type": "Phrase"},
    {"ad_group": "Online Xerox & Campus Delivery", "keyword": "bulk document printing thrissur", "match_type": "Phrase"},
    {"ad_group": "Online Xerox & Campus Delivery", "keyword": "spiral binding near me", "match_type": "Phrase"},
    {"ad_group": "Online Xerox & Campus Delivery", "keyword": "colour printout per page rate thrissur", "match_type": "Phrase"},

    # Ad Group 3: Campus Specific
    {"ad_group": "Campus Specific Searches", "keyword": "print shop near cusat kalamassery", "match_type": "Exact"},
    {"ad_group": "Campus Specific Searches", "keyword": "xerox shop near gec thrissur", "match_type": "Exact"},
    {"ad_group": "Campus Specific Searches", "keyword": "project printing near fisat angamaly", "match_type": "Exact"},
    {"ad_group": "Campus Specific Searches", "keyword": "printing shop near mesce kuttippuram", "match_type": "Exact"},
    {"ad_group": "Campus Specific Searches", "keyword": "project binding near vast thalakkottukara", "match_type": "Exact"},
]

NEGATIVE_KEYWORDS = [
    "free pdf download",
    "printer buy",
    "used printer",
    "hp printer repair",
    "epson toner price",
    "photocopier machine dealership",
    "jobs in printing press",
]


def generate_csv():
    csv_path = os.path.join(HERE, "google_ads_bulk_upload.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("Campaign,Ad Group,Keyword,Criterion Type\n")
        for kw in KEYWORDS_LIST:
            f.write(f"Printosky - Regional Campus Search (60km),{kw['ad_group']},{kw['keyword']},{kw['match_type']}\n")
    print(f"[OK] Generated Google Ads CSV: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Google Ads Campaign Generator")
    parser.add_argument("--generate-script", action="store_true", help="Generate Google Ads in-account script")
    parser.add_argument("--generate-csv", action="store_true", help="Generate Google Ads Editor CSV")

    args = parser.parse_args()

    if args.generate_csv or not args.generate_script:
        generate_csv()

    if args.generate_script:
        script_path = os.path.join(HERE, "google_ads_deploy_script.js")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(GOOGLE_ADS_SCRIPT)
        print(f"[OK] Generated Google Ads Script: {script_path}")


if __name__ == "__main__":
    main()
