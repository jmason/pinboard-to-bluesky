#!/usr/bin/python3
#
# based on https://gist.github.com/PSingletary/2396707785834418dab00e3d7a5c822f
# https://github.com/bluesky-social/atproto-website/blob/main/examples/create_bsky_post.py

# Fill in your auth details here (TODO move to an "env" file)

bsky_site = "https://bsky.social"

# the user for the bot Bluesky account
bsky_user = "..omitted....@jmason.org"

# Password for the account
bsky_password = "........omitted........."

# The Pinboard feed to gateway from
feed_url = "https://feeds.pinboard.in/rss/u:jm/"

# ------------------

import feedparser
import requests
from urllib.parse import urlencode
import sqlite3
import re
from datetime import datetime, timedelta, timezone
import os
import sys
import json
import argparse
from typing import Dict, List
from bs4 import BeautifulSoup


def bsky_login_session(pds_url: str, handle: str, password: str) -> Dict:
    resp = requests.post(
        pds_url + "/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
    )
    resp.raise_for_status()
    return resp.json()



def upload_file(pds_url, access_token, filename, img_bytes) -> Dict:
    suffix = filename.split(".")[-1].lower()
    mimetype = "application/octet-stream"
    if suffix in ["png"]:
        mimetype = "image/png"
    elif suffix in ["jpeg", "jpg"]:
        mimetype = "image/jpeg"
    elif suffix in ["webp"]:
        mimetype = "image/webp"

    # WARNING: a non-naive implementation would strip EXIF metadata from JPEG files here by default
    resp = requests.post(
        pds_url + "/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Content-Type": mimetype,
            "Authorization": "Bearer " + access_token,
        },
        data=img_bytes,
    )
    resp.raise_for_status()
    return resp.json()["blob"]



def fetch_embed_url_card(pds_url: str, access_token: str, url: str) -> Dict:
    # the required fields for an embed card
    card = {
        "uri": url,
        "title": "",
        "description": "",
    }

    # fetch the HTML
    try:
      resp = requests.get(url)
      resp.raise_for_status()
    except requests.exceptions.HTTPError:
      return # just don't use an embed card
    except requests.exceptions.ConnectionError:
      return # just don't use an embed card

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("meta", property="og:title")
    if title_tag:
        card["title"] = title_tag["content"]

    description_tag = soup.find("meta", property="og:description")
    if description_tag:
        card["description"] = description_tag["content"]

    max_image_file_size = 950000

    image_tag = soup.find("meta", property="og:image")
    if image_tag:
        img_url = image_tag["content"]
        if "http://localhost" in img_url:
            return # can't use this image, don't use a card
        if "://" not in img_url:
            img_url = url + img_url
        try:
          resp = requests.get(img_url)
          resp.raise_for_status()
          if len(resp.content) > max_image_file_size:
            return # just don't use an embed card
          card["thumb"] = upload_file(pds_url, access_token, img_url, resp.content)
        except requests.exceptions.HTTPError:
          return # just don't use an embed card

    return {
        "$type": "app.bsky.embed.external",
        "external": card,
    }


def parse_urls(text: str) -> List[Dict]:
    spans = []
    # partial/naive URL regex based on: https://stackoverflow.com/a/3809435
    # tweaked to disallow some training punctuation
    url_regex = rb"[$|\W](https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*[-a-zA-Z0-9@%_\+~#//=])?)"
    text_bytes = text.encode("UTF-8")
    for m in re.finditer(url_regex, text_bytes):
        spans.append(
            {
                "start": m.start(1),
                "end": m.end(1),
                "url": m.group(1).decode("UTF-8"),
            }
        )
    return spans


def parse_mentions(text: str) -> List[Dict]:
    spans = []
    # regex based on: https://atproto.com/specs/handle#handle-identifier-syntax
    mention_regex = rb"[$|\W](@([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    text_bytes = text.encode("UTF-8")
    for m in re.finditer(mention_regex, text_bytes):
        spans.append(
            {
                "start": m.start(1),
                "end": m.end(1),
                "handle": m.group(1)[1:].decode("UTF-8"),
            }
        )
    return spans


def parse_facets(post: Dict, pds_url: str, text: str, access_token: str) -> Dict:
    """
    parses post text and returns a list of app.bsky.richtext.facet objects for any mentions (@handle.example.com) or URLs (https://example.com)

    indexing must work with UTF-8 encoded bytestring offsets, not regular unicode string offsets, to match Bluesky API expectations
    """
    facets = []
    for m in parse_mentions(text):
        resp = requests.get(
            pds_url + "/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": m["handle"]},
        )
        # if handle couldn't be resolved, just skip it! will be text in the post
        if resp.status_code == 400:
            continue
        did = resp.json()["did"]
        facets.append(
            {
                "index": {
                    "byteStart": m["start"],
                    "byteEnd": m["end"],
                },
                "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}],
            }
        )

    link = ''
    for u in parse_urls(text):
        link = u["url"]
        facets.append(
            {
                "index": {
                    "byteStart": u["start"],
                    "byteEnd": u["end"],
                },
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        # NOTE: URI ("I") not URL ("L")
                        "uri": u["url"],
                    }
                ],
            }
        )

    if facets:
        post["facets"] = facets
        embed = fetch_embed_url_card(pds_url, access_token, link)
        if embed:
            post["embed"] = embed

    return post


def create_post(text, link):
    session = bsky_login_session(bsky_site, bsky_user, bsky_password)

    # trailing "Z" is preferred over "+00:00"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # these are the required fields which every post must include
    post = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": now,
    }

    # parse out mentions and URLs as "facets"
    # post text string always ends with the link
    if len(text) > 0:
        post = parse_facets(post, bsky_site, post["text"], session["accessJwt"])


    print("creating post:", file=sys.stderr)
    print(json.dumps(post, indent=2), file=sys.stderr)

    resp = requests.post(
        bsky_site + "/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": "Bearer " + session["accessJwt"]},
        json={
            "repo": session["did"],
            "collection": "app.bsky.feed.post",
            "record": post,
        },
    )
    print("createRecord response:", file=sys.stderr)
    print(json.dumps(resp.json(), indent=2))
    resp.raise_for_status()

# Parse the RSS feed
feed = feedparser.parse(feed_url)

one_week_ago = datetime.now() - timedelta(weeks=1)

# Connect to the SQLite database
conn = sqlite3.connect('rss_feed_tracker.db')
c = conn.cursor()

# Create a table to store processed items if it doesn't exist
c.execute('''CREATE TABLE IF NOT EXISTS processed_items
            (link TEXT PRIMARY KEY)''')

# Loop through each entry in the feed
for entry in reversed(feed.entries):
    # Extract the title, link, and description
    title = entry.title
    link = entry.link
    description = entry.description

    # strip HTML tags
    description = re.sub(r'</?blockquote>', '\"', description)
    description = re.sub(r'</?[A-Za-z]*>', '', description)

    # Check if the item has already been processed
    c.execute('SELECT * FROM processed_items WHERE link = ?', (link,))
    date = datetime(*entry.updated_parsed[:6])
    if c.fetchone() is None and date > one_week_ago:

        # Print the extracted information
        print(f"Title: {title}  Link: {link}")

        extralen = len(title) + len(link) + 4

        if len(description) > 296-extralen:
            shortdesc = description[:293-extralen]
            shortdesc = re.sub(r'\w+$', '', shortdesc.rstrip()).rstrip()
            description = shortdesc + " [\u2026]"

        # Make the POST request
        create_post(f"{title} \u2014 {description}\n\n{link}", link)
        
        # Check the response status code
        print(f"Successfully posted")

        # Mark the item as processed
        c.execute('INSERT INTO processed_items (link) VALUES (?)', (link,))
        conn.commit()

conn.close()

