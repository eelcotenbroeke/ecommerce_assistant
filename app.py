import streamlit as st
import requests
import re
from urllib.parse import urlparse, parse_qs, unquote

# Pagina configuratie
st.set_page_config(page_title="Price Sniper App", page_icon="🎯", layout="centered")

st.title("🎯 Universele E-Commerce Price Sniper")
st.write("Plak een product-URL om op prijs per ml en totalen te snipen.")

# Invoervelden
product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL...")
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

def extract_volume_ml(title, extensions=None, snippet="", link="", fallback_query=""):
    """Multi-bron parser: scant titel, snippets, extensions, URL en zoekt naar hoeveelheden."""
    combined_parts = [str(title), str(snippet), str(link), str(fallback_query)]
    if extensions:
        if isinstance(extensions, list):
            combined_parts.extend(str(e) for e in extensions)
        else:
            combined_parts.append(str(extensions))
            
    text_lower = " ".join(combined_parts).lower()
    
    # 1. Zoek naar expliciete vermenigvuldigingen (bijv. 3 x 400ml, 3x400ml, 3 stuks)
    multi_match = re.search(r'(\d+)\s*(?:[xXstuks]+\s*)*(\d+)\s*(ml|l)', text_lower)
    if multi_match:
        ex_match = re.search(r'(\d+)\s*[xX]\s*(\d+)\s*(ml|l)', text_lower)
        if ex_match:
            count = int(ex_match.group(1))
            amount = float(ex_match.group(2))
            unit = ex_match.group(3)
            if unit == 'l':
                amount *= 1000
            return count * amount
            
        stuks_match = re.search(r'(\d+)\s*stuks.*?(\d+)\s*(ml|l)', text_lower)
        if stuks_match:
            count = int(stuks_match.group(1))
            amount = float(stuks_match.group(2))
            unit = stuks_match.group(3)
            if unit == 'l':
                amount *= 1000
            return count * amount

    # 2. Zoek naar losse volumes (bijv. 400ml, 1.2l)
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
        st.warning("Vul zowel een product-URL als je SerpApi Key in!")
    else:
        with st.spinner("Markt aan het analyseren en slimme bundels herleiden..."):
            
            params = {
                "engine": "google_shopping",
                "q": search_query,
                "hl": "nl",
                "gl": "nl",
                "api_key": serp_key
            }
            
            raw_results = []
            seen_items = set()
            
            try:
                res = requests.get("https://serpapi.com/search", params=params, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    shopping_results = data.get("shopping_results", [])
                    
                    for item in shopping_results:
                        merchant = str(item.get("source", "Onbekende winkel")).lower()
                        price_str = str(item.get("price", "999999"))
                        link = item.get("link") or item.get("product_link") or item.get("direct_link")
                        title = item.get("title", "Onbekend product")
                        snippet = item.get("snippet", "")
                        extensions = item.get("extensions", [])
                        
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
                            total_ml = extract_volume_ml(title, extensions, snippet, link, search_query)
                            
                            raw_results.append({
                                "Store": merchant,
                                "Product": title,
                                "Price_Text": price_str,
                                "Numeric_Price": numeric_price,
                                "Total_Val": total_cost,
                                "Raw_ML": total_ml,
                                "Link": link,
                                "Shipping_Text": shipping_text
                            })
                            
                    # Stap 2: Bepaal de baseline (gemiddelde prijs van een losse fles van ~400ml)
                    single_prices = [r['Numeric_Price'] for r in raw_results if r['Raw_ML'] and 300 <= r['Raw_ML'] <= 500]
                    base_unit_price = sum(single_prices) / len(single_prices) if single_prices else 10.0
                    
                    comparison_results = []
                    for r in raw_results:
                        total_ml = r['Raw_ML']
                        
                        # Slimme fallback: Als volume onbekend is, maar de prijs is verdacht hoog (bijv. > 1.8x een losse fles), herleid multipack
                        if not total_ml or total_ml <= 0:
                            multiplier = round(r['Numeric_Price'] / base_unit_price)
                            if multiplier >= 2:
                                total_ml = 400 * multiplier
                                unit_details = f"Volume geschat ({multiplier}x pack / {int(total_ml)} ml) | Prijs per 100 ml: € {((r['Total_Val'] / total_ml) * 100):.2f}"
                                price_per_100ml = (r['Total_Val'] / total_ml) * 100
                            else:
                                total_ml = 400 # Standaard losse fles aanname
                                price_per_100ml = (r['Total_Val'] / total_ml) * 100
                                unit_details = f"Volume op basis van 400ml fles | Prijs per 100 ml: € {price_per_100ml:.2f}"
                        else:
                            price_per_100ml = (r['Total_Val'] / total_ml) * 100
                            unit_details = f"Volume: {int(total_ml)} ml | Prijs per 100 ml: € {price_per_100ml:.2f}"
                            
                        comparison_results.append({
                            "Store": r['Store'],
                            "Product": r['Product'],
                            "Total_Val": r['Total_Val'],
                            "Price_Per_100ml": price_per_100ml,
                            "Details": f"Product: € {r['Numeric_Price']:.2f} + Verzending: {r['Shipping_Text']} \n\n*{unit_details}*",
                            "Link": r['Link']
                        })
            except Exception as e:
                st.error(f"Fout bij ophalen: {e}")
                
            comparison_results = sorted(comparison_results, key=lambda x: x["Price_Per_100ml"])
            
            if comparison_results:
                msg = f"Gevonden deals voor: {search_query} ({len(comparison_results)} totaal, feilloos gesorteerd op prijs per ml)"
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
