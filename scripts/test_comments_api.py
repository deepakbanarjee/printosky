# -*- coding: utf-8 -*-
import requests

token = open('.env').read().split('INSTAGRAM_PAGE_ACCESS_TOKEN=')[1].split('\n')[0].strip().strip('\'\"')
ig_id = '17841468855448471'

url = f'https://graph.facebook.com/v20.0/{ig_id}/media'
params = {
    'fields': 'id,caption,comments_count',
    'access_token': token
}
r = requests.get(url, params=params)
print('Status:', r.status_code)
print('Response:', r.text)
