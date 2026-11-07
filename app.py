import streamlit as st
import requests
import re
from urllib.parse import urlparse, parse_qs, unquote

# Pagina configuratie
st.set_page_config(page_title="Price Sniper App", page_icon="🎯", layout="centered")

st.title("🎯 Universele E-Commerce Price Sniper")
st.write("Automatische prijs- en volume-sniper met directe webshop-links.")

# Product URL invoer
product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL...")

# SerpApi Key ophalen via Secrets (met fallback voor lokaal testen)
serp_key = ""
try:
    if "SERPAPI_KEY" in st.secrets:
        serp_key = st.secrets["SERPAPI_KEY"]
except Exception:
    pass

if not serp_key:
    serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

def extract_query_from_url(url):
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    for param in ['q', 'search', 'query', 'keyword', 'keywords']:
        if param in query_params:
            return unquote(query_params[param][0]).replace("+", " ")
    path_parts = parsed.path.lower().split('/')
    cleaned_words = []
    ignore_list = ['p', 'product', 'dp', 'nl', 'en', 'ref', 'item', 'g', 'c', 'f']
    for part in path_parts:
        if not part or part.isdigit() or len(part) <= 2:
            continue
        for sub in part.replace('_', '-').split('-'):
            if sub and sub not in ignore_list and not sub.startswith('b0') and not sub.isdigit():
                cleaned_words.append(sub)
    if cleaned_words:
        return " ".join(cleaned_words[:5])
    return "product"

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
search_query = st.text_input("Actieve zoekterm:", value=default_query)

if st.button("Snipe Prijzen", type="primary"):
    if not product_url or not serp_key:
        st.warning("Vul een product-URL in (en controleer of je SerpApi Key correct is ingesteld in de Secrets)!")
    else:
        with st.spinner("Markt aan het doorzoeken en schone links ophalen..."):
            
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
                res = requests.get("https://serpapi.com/search", params=params, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    shopping_results = data.get("shopping_results", [])
                    
                    for item in shopping_results:
                        merchant = str(item.get("source", "Onbekende winkel")).lower()
                        price_str = str(item.get("price", "999999"))
                        
                        # Prioriteer directe webshop links als die aanwezig zijn in de API response
                        link = (
                            item.get("direct_link") or 
                            item.get("offer_page_link") or 
                            item.get("product_link") or 
                            item.get("link")
                        )
                        
                        title = item.get("title", "Onbekend product")
                        snippet = item.get("snippet", "")
                        extensions = " ".join(str(e) for e in item.get("extensions", []))
                        
                        item_identifier = (merchant, price_str, title)
                        if item_identifier not in seen_items:
                            seen_items.add(item_identifier)
                            
                            try:
                                clean_price = price_str.replace("€", "").replace(".", "").replace(",", ".").strip()
                                numeric_price = float(clean_price)
                            except:
                                numeric_price = 999999.0
                                
                            if "bol.com" in merchant:
                                shipping_cost = 0.0
                                shipping_text = "€ 0,00 (Bol Select Voordeel)"
                            elif "amazon" in merchant:
                                shipping_cost = 0.0
                                shipping_text = "€ 0,00 (Amazon Prime Voordeel)"
                            else:
                                if numeric_price >= 40.0:
                                    shipping_cost = 0.0
                                    shipping_text = "€ 0,00 (Gratis verzending > €40)"
                                else:
                                    shipping_cost = 5.95
                                    shipping_text = "Ca. € 5,95"
                                    
                            total_cost = numeric_price + shipping_cost
                            full_text_context = f"{title} {snippet} {extensions}"
                            total_ml = extract_volume_ml(full_text_context)
                            
                            if total_ml and total_ml > 0:
                                price_per_100ml = (total_cost / total_ml) * 100
                                unit_details = f"Volume: {int(total_ml)} ml | Prijs per 100 ml: € {price_per_100ml:.2f}"
                            else:
                                price_per_100ml = 999999.0
                                unit_details = "Volume onbekend"
                                    
                            comparison_results.append({
                                "Store": merchant,
                                "Product": title,
                                "Price_Text": price_str,
                                "Total_Val": total_cost,
                                "Price_Per_100ml": price_per_100ml,
                                "Details": f"Product: € {numeric_price:.2f} + Verzending: {shipping_text} \n\n*{unit_details}*",
                                "Link": link
                            })
                            
                    for item in shopping_results[:3]:
                        product_id = item.get("product_id")
                        base_title = item.get("title", "Onbekend product")
                        
                        if product_id:
                            prod_params = {
                                "engine": "google_product",
                                "product_id": product_id,
                                "hl": "nl",
                                "gl": "nl",
                                "api_key": serp_key
                            }
                            try:
                                prod_res = requests.get("https://serpapi.com/search", params=prod_params, timeout=6)
                                if prod_res.status_code == 200:
                                    prod_data = prod_res.json()
                                    sellers = (
                                        prod_data.get("sellers_results", {}).get("online_sellers", []) or
                                        prod_data.get("offers", []) or
                                        prod_data.get("stores", [])
                                    )
                                    for seller in sellers:
                                        merchant = str(seller.get("name") or seller.get("source") or "Onbekende winkel").lower()
                                        price_str = str(seller.get("base_price") or seller.get("price") or "999999")
                                        
                                        link = (
                                            seller.get("direct_link") or 
                                            seller.get("offer_page_link") or 
                                            seller.get("link")
                                        )
                                        
                                        seller_title = seller.get("title") or base_title
                                        seller_snippet = seller.get("snippet", "")
                                        seller_ext = " ".join(str(e) for e in seller.get("extensions", []))
                                        
                                        item_id = (merchant, price_str, seller_title)
                                        if item_id in seen_items:
                                            continue
                                        seen_items.add(item_id)
                                        
                                        try:
                                            clean_price = price_str.replace("€", "").replace(".", "").replace(",", ".").strip()
                                            numeric_price = float(clean_price)
                                        except:
                                            numeric_price = 999999.0
                                            
                                        if "bol.com" in merchant:
                                            shipping_cost = 0.0
                                            shipping_text = "€ 0,00 (Bol Select Voordeel)"
                                        elif "amazon" in merchant:
                                            shipping_cost = 0.0
                                            shipping_text = "€ 0,00 (Amazon Prime Voordeel)"
                                        else:
                                            if numeric_price >= 40.0:
                                                shipping_cost = 0.0
                                                shipping_text = "€ 0,00 (Gratis verzending > €40)"
                                            else:
                                                shipping_cost = 5.95
                                                shipping_text = "Ca. € 5,95"
                                                
                                        total_cost = numeric_price + shipping_cost
                                        full_text_context = f"{seller_title} {seller_snippet} {seller_ext}"
                                        total_ml = extract_volume_ml(full_text_context)
                                        
                                        if total_ml and total_ml > 0:
                                            price_per_100ml = (total_cost / total_ml) * 100
                                            unit_details = f"Volume: {int(total_ml)} ml | Prijs per 100 ml: € {price_per_100ml:.2f}"
                                        else:
                                            price_per_100ml = 999999.0
                                            unit_details = "Volume onbekend"
                                                
                                        comparison_results.append({
                                            "Store": merchant,
                                            "Product": seller_title,
                                            "Price_Text": price_str,
                                            "Total_Val": total_cost,
                                            "Price_Per_100ml": price_per_100ml,
                                            "Details": f"Product: € {numeric_price:.2f} + Verzending: {shipping_text} \n\n*{unit_details}*",
                                            "Link": link
                                        })
                            except:
                                pass
            except Exception as e:
                st.error(f"Fout bij ophalen: {e}")
                
            comparison_results = sorted(comparison_results, key=lambda x: (x["Price_Per_100ml"] if x["Price_Per_100ml"] < 999999 else x["Total_Val"]))
            
            if comparison_results:
                msg = f"Gevonden deals voor: {search_query} ({len(comparison_results)} totaal, gesorteerd op prijs per ml)"
                st.success(msg)
                
                for item in comparison_results:
                    with st.container():
                        header_text = f"🛒 {item['Store'].upper()} — Totaal: € {item['Total_Val']:.2f}"
                        st.markdown(header_text.replace(".", ","))
                        st.write(f"**{item['Product']}**")
                        st.markdown(item['Details'].replace(".", ","))
                        
                        if item['Link']:
                            st.link_button(f"🔗 {item['Store'].upper()} Bezoeken", item['Link'])
                        else:
                            st.caption("Geen directe URL beschikbaar")
                            
                        st.divider()
            else:
                st.info("Geen resultaten gevonden.")
