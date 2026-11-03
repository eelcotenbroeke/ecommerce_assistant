import streamlit as st
import requests
from urllib.parse import urlparse, parse_qs

# Pagina configuratie
st.set_page_config(page_title="Price Sniper", page_icon="🎯", layout="centered")

st.title("🎯 E-Commerce Price Sniper")
st.write("Plak je product-URL van Bol.com of Amazon.nl om direct de scherpste deal te vinden.")

# Invoervelden
product_url = st.text_input("Product URL:", placeholder="Plak hier je Bol.com of Amazon.nl URL...")
serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

if st.button("Snipe Prijzen", type="primary"):
    if not product_url or not serp_key:
        st.warning("Vul zowel een product-URL als je SerpApi Key in!")
    else:
        with st.spinner("Markt aan het afstruinen..."):
            parsed = urlparse(product_url)
            domain = parsed.netloc.lower()
            query_params = parse_qs(parsed.query)
            
            # Zoektermen afleiden
            if "amazon" in domain:
                if 'keywords' in query_params:
                    keywords = query_params['keywords'][0].split()
                else:
                    keywords = ["product"]
            else:
                if 'q' in query_params:
                    keywords = query_params['q'][0].split()
                else:
                    path_parts = parsed.path.lower().split('/')
                    for part in path_parts:
                        if len(part) > 10 and not part.isdigit():
                            keywords = part.replace('-', ' ').split()[:4]
                            break
            
            query = " ".join(keywords) if keywords else "product"
            
            # SerpApi aanroepen
            params = {
                "engine": "google_shopping",
                "q": query,
                "hl": "nl",
                "gl": "nl",
                "api_key": serp_key
            }
            
            comparison_results = []
            
            try:
                res = requests.get("https://serpapi.com/search", params=params, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    shopping_results = data.get("shopping_results", [])
                    
                    for item in shopping_results:
                        merchant = item.get("source", "Onbekende winkel").lower()
                        price_str = item.get("price", "999999")
                        link = item.get("link")
                        title = item.get("title")
                        
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
                st.success(f"Gevonden zoekterm: *{query}*")
                
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
