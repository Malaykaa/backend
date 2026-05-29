"""Test rapide des actors Apify — 3 items max par actor pour economiser les credits.

Usage (depuis malaykaa-backend/) :
    python scripts/test_apify_actors.py
"""

import os
import sys
import json

def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))

API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
if not API_TOKEN:
    print("[ERREUR] APIFY_API_TOKEN absent du .env")
    sys.exit(1)

try:
    from apify_client import ApifyClient
except ImportError:
    print("[ERREUR] apify_client non installe — lance : pip install apify-client")
    sys.exit(1)

client = ApifyClient(API_TOKEN)

def run_test(label, actor_id, input_data):
    print(f"\n{'-'*60}")
    print(f"[TEST] {label}")
    print(f"  Actor : {actor_id}")
    print(f"  Input : {json.dumps(input_data, ensure_ascii=True)}")
    try:
        run = client.actor(actor_id).call(run_input=input_data, timeout_secs=120)
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            print("  [FAIL] Pas de dataset retourne")
            return
        result = client.dataset(dataset_id).list_items()
        items = result.items if hasattr(result, "items") else result.get("items", [])
        print(f"  [OK] {len(items)} item(s) retourne(s)")
        if items:
            first = items[0]
            preview = {k: str(v)[:100] for k, v in list(first.items())[:6]}
            print(f"  Exemple :")
            for k, v in preview.items():
                print(f"    {k}: {v}")
    except Exception as e:
        print(f"  [FAIL] Erreur : {e}")


# -- Tests 3 items max chacun -------------------------------------------------

run_test(
    label="Indeed Jobs - Nigeria (pays supporte)",
    actor_id="valig/indeed-jobs-scraper",
    input_data={"country": "ng", "location": "Nigeria", "maxItems": 3},
)

run_test(
    label="LinkedIn Jobs - Nigeria (champ urls[])",
    actor_id="curious_coder/linkedin-jobs-scraper",
    input_data={
        "urls":     ["https://www.linkedin.com/jobs/search/?keywords=emploi&location=Nigeria"],
        "maxCount": 3,
    },
)

run_test(
    label="LinkedIn Post Search - #emploiAfrique",
    actor_id="harvestapi/linkedin-post-search",
    input_data={
        "searchQueries":      ["#emploiAfrique"],
        "maxPosts":           3,
        "scrapeComments":     False,
        "scrapeReactions":    False,
        "postNestedComments": False,
    },
)

run_test(
    label="Google Jobs - Abidjan",
    actor_id="johnvc/Google-Jobs-Scraper",
    input_data={"query": "emploi", "location": "Abidjan, Cote d'Ivoire"},
)

run_test(
    label="LinkedIn Profile Posts - MyJobMag (champ limit)",
    actor_id="apimaestro/linkedin-profile-posts",
    input_data={"profileUrl": "https://www.linkedin.com/company/myjobmag/", "limit": 3},
)

run_test(
    label="Facebook Posts - page myjobmag",
    actor_id="apify/facebook-posts-scraper",
    input_data={
        "startUrls":    [{"url": "https://www.facebook.com/myjobmag"}],
        "resultsLimit": 3,
        "captionText":  False,
    },
)

run_test(
    label="Website Crawler - MyJobMag",
    actor_id="apify/website-content-crawler",
    input_data={
        "startUrls":     [{"url": "https://myjobmag.com/"}],
        "maxCrawlPages": 2,
        "maxCrawlDepth": 1,
        "saveMarkdown":  True,
        "blockMedia":    True,
    },
)

print(f"\n{'-'*60}")
print("[DONE] Tests termines.")
print("[IMPORTANT] Regenere ta cle Apify sur console.apify.com > Settings > API tokens")
