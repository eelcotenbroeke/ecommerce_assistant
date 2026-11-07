import streamlit as st
import requests
import re
from urllib.parse import urlparse, parse_qs, unquote

st.set_page_config(page_title="Price Sniper App", page_icon="🎯", layout="centered")

st.title("🎯 Universele E-Commerce Price Sniper")
st.write("Automatische prijs- en volume-sniper met directe winkel-zoeklinks.")

product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL...")

serp_key = ""
try:
    if "SERPAPI_KEY" in st.secrets:
        serp_key = st.secrets["SERPAPI_KEY"]
except Exception:
    pass

if not serp_key:
    serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

def extract_query_from_url(url):
    if not url:
        return ""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    for param in ['q', 'search', 'query', 'keyword', 'keywords']:
        if param in query_params:
            return unquote(query_params[param][0]).replace("+", " ")
            
    path_parts = parsed.path.lower().split('/')
    cleaned_words = []
    ignore_list = ['p', 'product', 'dp', 'nl', 'en', 'ref', 'item', 'g', 'c', 'f', 'bp']
    
    for part in path_parts:
        if not part or part.isdigit() or (len(part) <= 2 and part not in ['ml', 'l']):
            continue
        for sub in part.replace('_', '-').split('-'):
            if sub and sub not in ignore_list and not sub.startswith('b0') and not sub.isdigit():
                cleaned_words.append(sub)
                
    if cleaned_words:
        return " ".join(cleaned_words[:6])
        
    return "product"

def clean_store_link(raw_link, merchant="", product_title=""):
    if raw_link and "google.com" not in str(raw_link) and "http" in str(raw_link):
        return raw_link
        
    query = product_title.strip()
    merchant_lower = merchant.lower()
    
    if "bol.com" in merchant_lower:
        return f"https://www.bol.com/nl/nl/s/?searchtext={requests.utils.quote(query)}"
    elif "amazon" in merchant_lower:
        return f"https://www.amazon.nl/s?k={requests.utils.quote(query)}"
    elif "plein" in merchant_lower:
        return f"https://www.plein.nl/zoeken?q={requests.utils.quote(query)}"
    elif "parfumdreams" in merchant_lower:
        return f"https://www.parfumdreams.nl/Vat/Search.aspx?search={requests.utils.quote(query)}"
    else:
        return f"https://www.google.com/search?q={requests.utils.quote(merchant + ' ' + query)}"

def extract_volume_ml(text):
    text_lower = str(text).lower()
    
    multi_match = re.search(r'(\d+)\s*[xX]\s*(\d+)\s*(ml|l)', text_lower)
    if multi_match:
        count = int(multi_match.group(1))
        amount = float(multi_match.group(2))
        unit = multi_match.group(3)
        if unit == 'l':
            amount *= 1000
        return count * amount
        
    pack_match = re.search(r'(?:set van\s*)?(\d+)\s*(?:stuks|pack|x).*?(\d+)\s*(ml|l)', text_lower)
    if pack_match:
        count = int(pack_match.group(1))
        amount = float(pack_match.group(2))
        unit = pack_match.group(3)
        if unit == 'l':
            amount *= 1000
        return count * amount

    match = re.search(r'(\d+(?:[.,]\d+)?)\s*(ml|l)', text_lower)
    if match:
        val = float(match.group(1).replace(',', '.'))
        unit = match.group(2)
        if unit == 'l':
            return val * 1000
        return val
        
    return None

default_query = extract_query_from_url(product_url) if product_url else ""
search_query = st.text_input("Actieve zoekterm (inclusief gedetecteerd merk):", value=default_query)

if st.button("Snipe Prijzen", type="primary"):
    if not product_url or not serp_key:
        st.warning("Vul een product-URL in (en controleer of je SerpApi Key correct is ingesteld in de Secrets)!")
    else:
        with st.spinner("Markt aan het doorzoeken..."):
            params = {
                "engine": "google_shopping",
                "q": search_query,
                "hl": "nl",
                "gl": "nl",
                "api_key": serp_key
            }
            
            comparison_results = []
            seen_items = set()
            
            try:
                res = requests.get("https://serpapi.com/search", params=params, timeout=25)
                if res.status_code == 200:
                    data = res.json()
                    shopping_results = data.get("shopping_results", [])
                    
                    for item in shopping_results:
                        merchant = str
