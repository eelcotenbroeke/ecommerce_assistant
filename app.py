import streamlit as st
import requests
from urllib.parse import urlparse, parse_qs, unquote

# Pagina configuratie
st.set_page_config(page_title="Price Sniper App", page_icon="🎯", layout="centered")

st.title("🎯 Universele E-Commerce Price Sniper")
st.write("Plak een product-URL of voer handmatig een zoekterm in om alle deals (inclusief gesponsord) te snipen.")

# Invoervelden
product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL...")
serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

# Functie om universeel zoektermen uit URL's te halen
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
        sub_parts = part.replace('_', '-').split('-')
        for sub in sub_parts:
            if sub and sub not in ignore_list and not sub.startswith('b0') and not sub.isdigit():
                cleaned_words.append(sub)
                
    if cleaned_words:
        return " ".join(cleaned_words[:5])
        
    return "product"

default_query = extract_query_from_url(product_url) if product_url else ""
search_query = st.text_input("Actieve zoekterm:", value=default_query)

if st.button("Snipe Prijzen", type="primary"):
    if not product_url or not serp_key:
        st.warning("Vul zowel een product-URL als je SerpApi Key in!")
    else:
        with st.spinner("Markt inclusief advertenties aan het afstruinen..."):
            
            params = {
                "engine": "google_shopping",
                "q": search_query,
                "hl": "nl",
                "gl": "nl",
                "api_key": serp_key
            }
            
            comparison_results = []
            
            try:
                res = requests.get("https://serpapi.com/search", params=params, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    
                    # COMBINEER organische resultaten, inline/gesponsorde blokken én advertenties
                    shopping_results = data.get("shopping_results", [])
                    inline_results = data.get("inline_shopping_results", [])
                    ads_results = data.get("ads_results", [])
                    
                    all_items = shopping_results + inline_results + ads_results
                    
                    seen_items = set()
                    
                    for item in all_items:
                        merchant = item.get("source", "Onbekende winkel").lower()
                        price_str = item.get("price", "999999")
                        link = item.get("link")
                        title = item.get("title")
                        
                        # Voorkom dubbele vermeldingen in de lijst
                        item_identifier = (merchant, price_str, title)
                        if item_identifier in seen_items:
                            continue
                        seen_items.add(item_identifier)
                        
                        try:
                            numeric_price = float(price_str.replace("€", "").replace(".", "").replace(",", ".").strip())
                        except:
                            numeric_price = 999999.0
                        
                        # Verzendkosten logica met abonnementsvoordeel
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
                                shipping_text = "Ca. € 5,95 (of check drempel)"
                        
                        total_cost = numeric_price + shipping_cost
                        
                        comparison_results.append({
                            "Store": merchant,
                            "Product": title,
                            "Price_Text": price_str,
                            "Total_Val": total_cost,
                            "Details": f"Product: {price_str} + Verzending: {shipping_text}",
                            "Link": link
                        })
            except Exception as e:
                st.error(f"Fout bij ophalen: {e}")
                
            # Sorteer op totaalprijs
            comparison_results = sorted(comparison_results, key=lambda x: x["Total_Val"])
            
            if comparison_results:
                st.success(f"Gevonden deals voor: *{search_query}* (Inclusief Gesponsord)")
                
                for item in comparison_results:
                    with st.container():
                        st.markdown(f"### 🛒 {item['Store'].upper()} — Totaal: € {item['Total_Val']:.2f}".replace(".", ","))
                        st.write(f"**{item['Product']}**")
                        st.write(f"*{item['Details']}*")
                        if item['Link']:
                            st.markdown(f"[Direct naar winkel]({item['Link']})")
                        st.divider()
            else:
                st.info("Geen resultaten gevonden.")
