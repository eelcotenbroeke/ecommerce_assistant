import streamlit as st
import requests
from urllib.parse import urlparse, parse_qs, unquote

# Pagina configuratie
st.set_page_config(page_title="Price Sniper App", page_icon="🎯", layout="centered")

st.title("🎯 Universele E-Commerce Price Sniper")
st.write("Plak een product-URL van Bol, Amazon, Coolblue, MediaMarkt, Etos, Kruidvat of Gamma om de scherpste deal te vinden.")

# Invoervelden
product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL van je favoriete winkel...")
serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

# Functie om universeel zoektermen uit willekeurige winkel-URL's te halen
def extract_query_from_url(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    query_params = parse_qs(parsed.query)
    
    # 1. Check voor bekende zoekparameters in de URL (zoals bij Amazon of webshops met ?q= of ?search=)
    for param in ['q', 'search', 'query', 'keyword', 'keywords']:
        if param in query_params:
            return unquote(query_params[param][0]).replace("+", " ")
            
    # 2. Haal nuttige woorden uit het webadres (path) voor winkels zoals Bol, Coolblue, MediaMarkt, etc.
    path_parts = parsed.path.lower().split('/')
    cleaned_words = []
    
    # Woorden/stukjes die we willen negeren omdat ze geen productnaam zijn
    ignore_list = ['p', 'product', 'dp', 'nl', 'en', 'ref', 'item', 'g', 'c', 'f']
    
    for part in path_parts:
        if not part or part.isdigit() or len(part) <= 2:
            continue
        # Soms staat een producttitel met streepjes ertussen (bijv. "de-dappere-ridder")
        sub_parts = part.replace('_', '-').split('-')
        for sub in sub_parts:
            if sub and sub not in ignore_list and not sub.startswith('b0') and not sub.isdigit():
                cleaned_words.append(sub)
                
    if cleaned_words:
        # Neem de eerste relevante woorden uit de URL (maximaal 4 à 5 voor een zuivere zoekopdracht)
        return " ".join(cleaned_words[:5])
        
    return "product"

# Bepaal slim de zoekterm als er een URL is ingevoerd
default_query = extract_query_from_url(product_url) if product_url else ""

# Geef de gebruiker de optie om de afgeleide zoekterm eventueel aan te passen
search_query = st.text_input("Afgeleide zoekterm (eventueel handmatig aan te passen):", value=default_query)

if st.button("Snipe Prijzen", type="primary"):
    if not product_url or not serp_key:
        st.warning("Vul zowel een product-URL als je SerpApi Key in!")
    else:
        with st.spinner("Markt aan het afstruinen..."):
            
            # SerpApi aanroepen met de scherpe zoekterm
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
                st.success(f"Actieve zoekterm: *{search_query}*")
                
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
