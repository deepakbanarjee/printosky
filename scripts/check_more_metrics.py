# -*- coding: utf-8 -*-
import json
import requests

token = open('.env').read().split('INSTAGRAM_PAGE_ACCESS_TOKEN=')[1].split('\n')[0].strip().strip('\'\"')
ig_id = '17841468855448471'

metrics = ['views', 'profile_views', 'website_clicks', 'follower_count']
for m in metrics:
    r = requests.get(
        f'https://graph.facebook.com/v20.0/{ig_id}/insights',
        params={'metric': m, 'metric_type': 'total_value', 'period': 'day', 'access_token': token}
    ).json()
    if 'data' in r:
        val = r['data'][0].get('total_value', {}).get('value')
        print(f"Metric '{m}': {val}")
    else:
        print(f"Metric '{m}': {r.get('error', {}).get('message')}")
