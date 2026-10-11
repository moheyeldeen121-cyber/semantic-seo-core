import urllib.parse
from flask import Blueprint, jsonify, request
import config

bp = Blueprint("scraper", __name__)

@bp.route("/api/scrape-serp", methods=["POST"])
def fetch_serp():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "") or "").strip()[:300]
    location = str(payload.get("location", "") or "").strip()[:120]
    gl = str(payload.get("gl", "wt") or "wt").strip().lower()
    try:
        num = min(100, max(10, int(payload.get("num", 40))))
    except (TypeError, ValueError):
        num = 40

    if not query:
        return jsonify({"error": "Query is required"}), 400

    country_map = {
        "السعودية": "sa", "saudi arabia": "sa", "ksa": "sa", "المملكة العربية السعودية": "sa",
        "مصر": "eg", "egypt": "eg",
        "الامارات": "ae", "الإمارات": "ae", "uae": "ae", "united arab emirates": "ae",
        "الكويت": "kw", "kuwait": "kw",
        "قطر": "qa", "qatar": "qa",
        "عمان": "om", "oman": "om", "سلطنة عمان": "om",
        "البحرين": "bh", "bahrain": "bh",
        "الاردن": "jo", "الأردن": "jo", "jordan": "jo",
        "المغرب": "ma", "morocco": "ma",
        "الجزائر": "dz", "algeria": "dz",
        "تونس": "tn", "tunisia": "tn",
        "العراق": "iq", "iraq": "iq",
        "سوريا": "sy", "syria": "sy",
        "لبنان": "lb", "lebanon": "lb",
        "اليمن": "ye", "yemen": "ye",
        "فلسطين": "ps", "palestine": "ps",
        "ليبيا": "ly", "libya": "ly",
        "السودان": "sd", "sudan": "sd"
    }

    loc_lower = location.lower()
    if loc_lower in country_map:
        gl = country_map[loc_lower]
        location = ""  # Do not append country to query

    # DO NOT mix English canonical names with Arabic queries.
    # If the user typed "الرياض", location is "الرياض". We append it directly.
    # E.g. "مكتب محاماة" + "الرياض" -> "مكتب محاماة الرياض"
    local_query = f"{query} {location}" if location else query

    # Map Google's gl parameter (e.g. 'sa', 'eg') to DuckDuckGo's region (e.g. 'sa-ar', 'eg-ar')
    # Default to global 'wt-wt' if not found.
    ddg_region = f"{gl}-ar" if gl and gl != "wt" else "wt-wt"

    try:
        # Optional dependency, imported lazily: a missing package used to
        # crash the WHOLE app at startup because app.py imports this module.
        from ddgs import DDGS
    except Exception:
        return jsonify({"error": "SERP search needs the optional 'ddgs' package (pip install ddgs)."}), 501

    urls = []
    titles = []
    try:
        ddg = DDGS()
        search_results = ddg.text(local_query, region=ddg_region, max_results=num)
        
        if not search_results:
            return jsonify({"error": "No results found or IP blocked by search engine. Try again later."}), 500
            
        for item in search_results:
            urls.append(item.get("href", ""))
            titles.append(item.get("title", ""))
            
    except Exception as exc:
        return jsonify({"error": f"Scraping failed: {str(exc)}"}), 500

    return jsonify({"urls": urls, "titles": titles})
