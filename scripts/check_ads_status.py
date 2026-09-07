# -*- coding: utf-8 -*-
import json
import requests

token = open('.env').read().split('INSTAGRAM_PAGE_ACCESS_TOKEN=')[1].split('\n')[0].strip().strip('\'\"')
ig_id = '17841468855448471'

print("================================================================")
print(" CHECKING META ADS & PROMOTIONS STATUS")
print("================================================================")

# 1. Check Ad Accounts linked to this token
r_act = requests.get(
    'https://graph.facebook.com/v20.0/me/adaccounts',
    params={'fields': 'id,name,account_status,amount_spent,currency,balance', 'access_token': token}
).json()
print("1. Ad Accounts:", json.dumps(r_act, indent=2))

# 2. Check if the Business Manager has Ad Accounts
r_bm = requests.get(
    'https://graph.facebook.com/v20.0/me/businesses',
    params={'fields': 'id,name,adaccounts{id,name,account_status,amount_spent}', 'access_token': token}
).json()
print("\n2. Businesses & Ad Accounts:", json.dumps(r_bm, indent=2))

# 3. Check Media & Promotion status for our 2 posts
posts = ['18131474977641076', '18116875022291814']
for pid in posts:
    r_p = requests.get(
        f'https://graph.facebook.com/v20.0/{pid}',
        params={'fields': 'id,like_count,comments_count,media_type,timestamp,permalink', 'access_token': token}
    ).json()
    print(f"\n--- Post {pid} ---")
    print(json.dumps(r_p, indent=2))

    # Check insights
    r_ins = requests.get(
        f'https://graph.facebook.com/v20.0/{pid}/insights',
        params={'metric': 'reach,saved,total_interactions', 'access_token': token}
    ).json()
    print("Insights:", json.dumps(r_ins.get('data', []), indent=2))
