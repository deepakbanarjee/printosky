# -*- coding: utf-8 -*-
import json
import requests

token = open('.env').read().split('INSTAGRAM_PAGE_ACCESS_TOKEN=')[1].split('\n')[0].strip().strip('\'\"')
page_id = '999350999936953'
ig_id = '17841468855448471'

print("=== Checking Page Ads & Promoted Posts ===")
r = requests.get(
    f'https://graph.facebook.com/v20.0/{page_id}',
    params={'fields': 'ads_posts,promoted_posts', 'access_token': token}
).json()
print("Page result:", json.dumps(r, indent=2))

print("\n=== Checking IG Business Insights & Profile Visits ===")
# Let's check profile_views / website_clicks on IG account
r_ig = requests.get(
    f'https://graph.facebook.com/v20.0/{ig_id}/insights',
    params={'metric': 'reach,total_interactions,accounts_engaged', 'metric_type': 'total_value', 'period': 'day', 'access_token': token}
).json()
print("IG insights:", json.dumps(r_ig, indent=2))
