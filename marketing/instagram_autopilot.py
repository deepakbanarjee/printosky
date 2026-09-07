# -*- coding: utf-8 -*-
"""Instagram Autopilot Engine for Printosky (@printosky_official).

Connects directly to Meta Graph API (v20.0) using credentials in .env to:
1. Check live profile health & follower growth
2. Publish single images, carousels, and Reels
3. Monitor comments & trigger automated responses
4. Track student engagement & sends-per-reach

Usage:
  python marketing/instagram_autopilot.py --status
  python marketing/instagram_autopilot.py --recent-posts
  python marketing/instagram_autopilot.py --publish-image --image-url "https://..." --caption "..."
  python marketing/instagram_autopilot.py --publish-reel --video-url "https://..." --caption "..."
"""

import argparse
import json
import os
import sys
import time
import requests

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

GRAPH_VERSION = "v20.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


def load_credentials():
    """Load Instagram and Meta credentials from .env."""
    env_path = ".env"
    creds = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip().strip('"').strip("'")

    token = creds.get("INSTAGRAM_PAGE_ACCESS_TOKEN") or creds.get("META_SYSTEM_USER_TOKEN")
    account_id = creds.get("INSTAGRAM_ACCOUNT_ID")
    page_id = creds.get("META_PAGE_ID")

    if not token or not account_id:
        print("[ERROR] Missing INSTAGRAM_PAGE_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID in .env.")
        sys.exit(1)

    return token, account_id, page_id


def get_profile_status(token, account_id, page_id):
    """Fetch live account stats from Instagram Graph API."""
    fields = "id,username,name,biography,profile_picture_url,followers_count,follows_count,media_count,website"
    url = f"{BASE_URL}/{account_id}?fields={fields}&access_token={token}"
    resp = requests.get(url)
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Meta Graph API Error: {data['error']['message']}")

    print("================================================================")
    print(" PRINTOSKY INSTAGRAM COMMAND CENTER (@printosky_official)")
    print("================================================================")
    print(f" Account Handle : @{data.get('username')}")
    print(f" Business Name  : {data.get('name', 'Printosky')}")
    print(f" Instagram ID   : {data.get('id')}")
    print(f" Linked Page ID : {page_id}")
    print(f" Followers      : {data.get('followers_count', 0)}")
    print(f" Following      : {data.get('follows_count', 0)}")
    print(f" Total Posts    : {data.get('media_count', 0)}")
    bio_clean = data.get('biography', 'N/A')
    try:
        print(f" Bio            : {bio_clean}")
    except UnicodeEncodeError:
        print(f" Bio            : {bio_clean.encode('ascii', 'replace').decode()}")
    print("----------------------------------------------------------------")
    print(" [STATUS] Connection is LIVE and healthy! Ready for automation.")
    print("================================================================")
    return data


def list_recent_posts(token, account_id, limit=5):
    """List recent posts with engagement stats."""
    fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count"
    url = f"{BASE_URL}/{account_id}/media?fields={fields}&limit={limit}&access_token={token}"
    resp = requests.get(url)
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Error fetching media: {data['error']['message']}")

    posts = data.get("data", [])
    if not posts:
        print("\nNo posts published yet on @printosky_official.")
        print("Ready to publish your first post or Reel!")
        return []

    print(f"\n--- Recent Posts ({len(posts)}) ---")
    for idx, post in enumerate(posts, 1):
        caption_preview = (post.get("caption") or "").replace("\n", " ")[:60]
        print(f"[{idx}] ID: {post.get('id')} | Type: {post.get('media_type')}")
        print(f"    Date: {post.get('timestamp')} | Likes: {post.get('like_count', 0)} | Comments: {post.get('comments_count', 0)}")
        print(f"    Caption: \"{caption_preview}...\"")
        print(f"    Link: {post.get('permalink')}")
    return posts


def publish_image_post(token, account_id, image_url, caption):
    """Publish a single image post to Instagram feed."""
    print(f"Creating media container for image...")
    create_url = f"{BASE_URL}/{account_id}/media"
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    }
    resp = requests.post(create_url, data=payload)
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Container creation failed: {data['error']['message']}")

    creation_id = data["id"]
    print(f"[OK] Container created (ID: {creation_id}). Publishing...")

    publish_url = f"{BASE_URL}/{account_id}/media_publish"
    pub_payload = {
        "creation_id": creation_id,
        "access_token": token,
    }
    pub_resp = requests.post(publish_url, data=pub_payload)
    pub_data = pub_resp.json()

    if "error" in pub_data:
        raise RuntimeError(f"Publish failed: {pub_data['error']['message']}")

    post_id = pub_data["id"]
    print(f"\n[SUCCESS] Post successfully published to Instagram! Post ID: {post_id}")
    return post_id


def publish_reel(token, account_id, video_url, caption, cover_url=None):
    """Publish a Reel to Instagram feed and Reels tab."""
    print(f"Creating Reel container...")
    create_url = f"{BASE_URL}/{account_id}/media"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    }
    if cover_url:
        payload["cover_url"] = cover_url

    resp = requests.post(create_url, data=payload)
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Reel container creation failed: {data['error']['message']}")

    creation_id = data["id"]
    print(f"[OK] Reel container created (ID: {creation_id}). Waiting for video upload processing...")

    # Wait for video processing by Instagram servers
    status_url = f"{BASE_URL}/{creation_id}?fields=status_code&access_token={token}"
    for _ in range(20):
        time.sleep(4)
        s_resp = requests.get(status_url).json()
        status_code = s_resp.get("status_code")
        print(f"    Processing status: {status_code}...")
        if status_code == "FINISHED":
            break
        elif status_code == "ERROR":
            raise RuntimeError("Instagram failed to process the video upload.")

    publish_url = f"{BASE_URL}/{account_id}/media_publish"
    pub_payload = {
        "creation_id": creation_id,
        "access_token": token,
    }
    pub_resp = requests.post(publish_url, data=pub_payload)
    pub_data = pub_resp.json()

    if "error" in pub_data:
        raise RuntimeError(f"Reel publish failed: {pub_data['error']['message']}")

    post_id = pub_data["id"]
    print(f"\n[SUCCESS] Reel published live on Instagram! Reel ID: {post_id}")
    return post_id


def main():
    parser = argparse.ArgumentParser(description="Instagram Autopilot for Printosky")
    parser.add_argument("--status", action="store_true", help="Check live Instagram connection and follower stats")
    parser.add_argument("--recent-posts", action="store_true", help="List recent posts and engagement metrics")
    parser.add_argument("--publish-image", action="store_true", help="Publish a photo to Instagram")
    parser.add_argument("--publish-reel", action="store_true", help="Publish a video Reel to Instagram")
    parser.add_argument("--image-url", type=str, help="Public URL of the image to post")
    parser.add_argument("--video-url", type=str, help="Public URL of the video (Reel) to post")
    parser.add_argument("--caption", type=str, default="", help="Caption for the post (including emojis and hashtags)")

    args = parser.parse_args()

    token, account_id, page_id = load_credentials()

    if args.status or (not any([args.recent_posts, args.publish_image, args.publish_reel])):
        get_profile_status(token, account_id, page_id)

    if args.recent_posts:
        list_recent_posts(token, account_id)

    if args.publish_image:
        if not args.image_url:
            print("Error: --image-url is required to publish an image.")
            sys.exit(1)
        publish_image_post(token, account_id, args.image_url, args.caption)

    if args.publish_reel:
        if not args.video_url:
            print("Error: --video-url is required to publish a Reel.")
            sys.exit(1)
        publish_reel(token, account_id, args.video_url, args.caption)


if __name__ == "__main__":
    main()
