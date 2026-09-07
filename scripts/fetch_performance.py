# -*- coding: utf-8 -*-
import json
import requests

token = open('.env').read().split('INSTAGRAM_PAGE_ACCESS_TOKEN=')[1].split('\n')[0].strip().strip('\'\"')
ig_id = '17841468855448471'
post_id = '18131474977641076'

print("================================================================")
print(" LIVE INSTAGRAM PERFORMANCE REPORT (@printosky_official)")
print("================================================================")

# 1. Account Info
r_acc = requests.get(
    f'https://graph.facebook.com/v20.0/{ig_id}',
    params={'fields': 'id,username,name,followers_count,follows_count,media_count', 'access_token': token}
).json()
print(f"Followers       : {r_acc.get('followers_count')}")
print(f"Following       : {r_acc.get('follows_count')}")
print(f"Total Posts     : {r_acc.get('media_count')}")

# 2. Post Engagement
r_post = requests.get(
    f'https://graph.facebook.com/v20.0/{post_id}',
    params={'fields': 'id,like_count,comments_count,media_type,timestamp,permalink', 'access_token': token}
).json()
print(f"\n--- Post #1 Status ---")
print(f"Type            : {r_post.get('media_type')}")
print(f"Likes           : {r_post.get('like_count', 0)}")
print(f"Comments        : {r_post.get('comments_count', 0)}")
print(f"Published At    : {r_post.get('timestamp')}")
print(f"Link            : {r_post.get('permalink')}")

# 3. Post Insights
r_ins = requests.get(
    f'https://graph.facebook.com/v20.0/{post_id}/insights',
    params={'metric': 'reach,saved,total_interactions', 'access_token': token}
).json()

print(f"\n--- Post Insights ---")
if 'data' in r_ins:
    for item in r_ins['data']:
        title = item.get('title', item.get('name'))
        val = item.get('values', [{}])[0].get('value', item.get('total_value', {}).get('value'))
        print(f"  {title:<22}: {val}")
else:
    print(f"  Insights note: {r_ins.get('error', {}).get('message', 'No insight data yet')}")

# 4. Comments list
r_comms = requests.get(
    f'https://graph.facebook.com/v20.0/{post_id}/comments',
    params={'fields': 'id,from,text,timestamp', 'access_token': token}
).json()
comments = r_comms.get('data', [])
print(f"\nRecent Comments : {len(comments)}")
for c in comments:
    user = c.get('from', {}).get('username', 'user')
    print(f"  @{user}: {c.get('text')}")

print("================================================================")
