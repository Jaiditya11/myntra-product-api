from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urlparse

app = FastAPI(title="Myntra Product POC API")


# ---------- MODELS ----------

class ProductRequest(BaseModel):
    url: HttpUrl


class ProductResponse(BaseModel):
    title: str
    primary_image: str
    original_price: int
    discounted_price: int
    in_stock: bool


# ---------- CORE SCRAPER LOGIC ----------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.6261.94 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def fetch_myntra_product(url: str) -> ProductResponse:
    # 1. Basic validation – only allow myntra.com
    parsed = urlparse(url)
    if "myntra.com" not in parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Only myntra.com product URLs are supported",
        )

    # 2. Fetch HTML
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Network error while fetching Myntra page: {e}",
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Myntra returned HTTP {resp.status_code}",
        )

    html = resp.text
    
    if "Access Denied" in html or "You don't have permission" in html:
        raise HTTPException(
        status_code=403,
        detail="Myntra blocked the request (Access Denied). Try again or use another proxy."
    )
    # 3. Parse HTML & locate the script that contains pdpData
    soup = BeautifulSoup(html, "lxml")

    script_text = None
    for script in soup.find_all("script"):
        text = script.get_text(strip=True)
        if "pdpData" in text:
            script_text = text
            break

    if not script_text:
        raise HTTPException(
            status_code=500,
            detail="Unable to locate product data (pdpData) in page",
        )

    # 4. Extract JSON portion from the script
    try:
        json_str = script_text[script_text.index("{"):]
        raw_data = json.loads(json_str)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse product JSON: {e}",
        )

    # Some pages may nest under pdpData or similar key
    data = raw_data.get("pdpData") or raw_data

    # 5. Map to our normalized response fields, with fallbacks

    # Title
    title = (
        data.get("name")
        or data.get("product", {}).get("productName")
        or "Unknown Product"
    )

    # Images – try secureSrc, then imageURL, then src
    primary_image = None
    media = data.get("media") or {}
    albums = media.get("albums") or []

    if albums and isinstance(albums, list):
        images = albums[0].get("images") or []
        if images:
            img = images[0]
            primary_image = (
                img.get("secureSrc")
                or img.get("imageURL")
                or img.get("src")
            )

    if not primary_image:
        primary_image = ""  # or raise if you want to enforce it

    # Pricing
    price_info = data.get("price") or {}

    original_price = (
        data.get("mrp")
        or price_info.get("mrp")
        or price_info.get("marked")
        or 0
    )

    discounted_price = (
        data.get("discountedPrice")
        or price_info.get("discountedPrice")
        or price_info.get("effective")
        or original_price
    )

    # Stock
    flags = data.get("flags") or {}
    in_stock = not flags.get("outOfStock", False)

    # 6. Return as ProductResponse model
    return ProductResponse(
        title=title,
        primary_image=primary_image,
        original_price=int(original_price or 0),
        discounted_price=int(discounted_price or 0),
        in_stock=bool(in_stock),
    )


# ---------- API ENDPOINT ----------

@app.post("/api/myntra/product", response_model=ProductResponse)
def get_myntra_product(payload: ProductRequest):
    """
    Accepts a Myntra product URL and returns:
    title, primary_image, original_price, discounted_price, in_stock
    """
    return fetch_myntra_product(str(payload.url))

