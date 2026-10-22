"""Synthetic data generator for a dry-fruit e-commerce seller (India). Run from repo root: python scripts/generate_data.py"""
import random
from datetime import date, timedelta
import numpy as np
import pandas as pd

random.seed(7); np.random.seed(7)
END = date(2026, 10, 22); START = END - timedelta(days=180)
NAVRATRI = (date(2026, 10, 11), date(2026, 10, 19))   # fasting-food spike
RAKHI = (date(2026, 8, 20), date(2026, 8, 28))        # gifting spike

# ---- Products: category, cost, price (margin = (price-cost)/price), stock, festival boost
P = [
 ("Makhana Roasted Plain 250g","Makhana",140,220,120,"navratri"),
 ("Makhana Peri-Peri 100g","Makhana",70,120,90,"navratri"),
 ("Makhana Raw 500g","Makhana",260,410,60,"navratri"),
 ("Almonds California 500g","Nuts",380,560,80,"gifting"),
 ("Cashew W320 500g","Nuts",420,620,70,"gifting"),
 ("Pistachio Salted 250g","Nuts",330,480,40,"gifting"),
 ("Walnut Kernels 250g","Nuts",210,320,55,"gifting"),
 ("Raisins Seedless 500g","Dried Fruit",110,170,150,None),
 ("Dried Figs (Anjeer) 250g","Dried Fruit",230,340,35,"gifting"),
 ("Dates Medjool 500g","Dried Fruit",300,450,45,"navratri"),
 ("Dry Fruit Gift Box 1kg","Gift Box",600,950,30,"gifting"),
 ("Trail Mix Energy 300g","Mixes",150,250,110,None),
 ("Sabudana 500g","Fasting",40,75,200,"navratri"),
 ("Kuttu Atta 500g","Fasting",45,80,160,"navratri"),
 ("Singhara Atta 500g","Fasting",55,95,140,"navratri"),
 ("Rock Salt (Sendha) 1kg","Fasting",25,50,180,"navratri"),
 ("Chia Seeds 250g","Seeds",90,160,100,None),
 ("Pumpkin Seeds 250g","Seeds",120,200,80,None),
 ("Peanut Chikki 200g","Snacks",40,80,170,None),
 ("Dry Fruit Laddu 250g","Snacks",160,260,25,"gifting"),
]
products = pd.DataFrame(P, columns=["product_name","category","cost_price","price","stock_units","festival_tag"])
products.insert(0,"product_id",[f"P{i+1:03d}" for i in range(len(products))])
products["margin_pct"] = ((products.price-products.cost_price)/products.price*100).round(1)

# ---- Customers with latent segment that drives behaviour
cities = ["Ludhiana","Amritsar","Jalandhar","Chandigarh","Patiala","Mohali","Delhi","Gurugram","Jaipur","Lucknow"]
fn = ["Aarav","Priya","Rohit","Simran","Harpreet","Neha","Karan","Anjali","Gurpreet","Pooja","Vikram","Sneha","Arjun","Meera","Manpreet","Ritu","Amit","Kavita","Jaspreet","Divya"]
ln = ["Sharma","Singh","Kaur","Verma","Gupta","Mehta","Bansal","Chopra","Jain","Arora","Malhotra","Sethi"]
SEGS = {"loyal":0.18,"regular":0.30,"price_sensitive":0.20,"new":0.12,"lapsing":0.12,"dormant":0.08}
# (orders per 30d at baseline, discount-needed prob, avg basket items)
BEH = {"loyal":(2.2,0.15,3.2),"regular":(1.0,0.3,2.4),"price_sensitive":(0.8,0.8,2.5),
       "new":(0.9,0.4,1.8),"lapsing":(0.9,0.35,2.3),"dormant":(0.5,0.4,2.0)}
N = 220; seg_list = np.random.choice(list(SEGS), N, p=list(SEGS.values()))
cust = []
for i, s in enumerate(seg_list):
    if s == "new": join = END - timedelta(days=random.randint(5, 45))
    else: join = START - timedelta(days=random.randint(0, 400))
    cust.append(dict(customer_id=f"C{i+1:04d}", name=f"{random.choice(fn)} {random.choice(ln)}",
        city=random.choice(cities), age=int(np.clip(np.random.normal(34,9),18,65)),
        acquisition_channel=random.choices(["Instagram","WhatsApp referral","Google","Direct","Marketplace"],[.3,.25,.2,.15,.1])[0],
        join_date=join, marketing_opt_in=random.random()<0.88,
        preferred_category=random.choice(["Makhana","Nuts","Dried Fruit","Fasting","Seeds","Snacks","Gift Box"]),
        _seg=s))
cdf = pd.DataFrame(cust)

# ---- Orders
pcat = products.set_index("product_id")
orders, items = [], []; oid = 1
def festival_w(d, tag):
    if tag == "navratri" and NAVRATRI[0]-timedelta(days=5) <= d <= NAVRATRI[1]: return 4.0
    if tag == "gifting" and RAKHI[0]-timedelta(days=4) <= d <= RAKHI[1]: return 3.0
    if tag == "gifting" and d >= date(2026,10,12): return 1.8   # pre-Diwali ramp
    return 1.0
for _, c in cdf.iterrows():
    base, disc_p, bsz = BEH[c._seg]
    # activity window per segment
    if c._seg == "dormant": lo, hi = START, END - timedelta(days=75)
    elif c._seg == "lapsing": lo, hi = START, END - timedelta(days=40)
    elif c._seg == "new": lo, hi = c.join_date, END
    else: lo, hi = max(START, c.join_date), END
    if lo >= hi: continue
    days = (hi-lo).days; n = np.random.poisson(base*days/30)
    if c._seg == "loyal": n = max(n, 4)
    d_list = sorted({lo + timedelta(days=int(x)) for x in np.random.uniform(0, days, n)})
    # bias loyal/regular towards festival windows
    for d in d_list:
        k = max(1, int(np.random.poisson(bsz)))
        w = np.array([festival_w(d, t) * (2.5 if cat == c.preferred_category else 1)
                      for t, cat in zip(products.festival_tag, products.category)], float)
        w /= w.sum()
        picks = np.random.choice(products.product_id, size=min(k,6), replace=False, p=w)
        used_disc = random.random() < disc_p
        dpct = random.choice([5,10,15,20]) if used_disc else 0
        tot = 0
        for pid in picks:
            q = random.choices([1,2,3],[.7,.2,.1])[0]; pr = pcat.loc[pid,"price"]
            tot += pr*q*(1-dpct/100)
            items.append(dict(order_id=f"O{oid:05d}", product_id=pid, quantity=q, unit_price=pr))
        orders.append(dict(order_id=f"O{oid:05d}", customer_id=c.customer_id, order_date=d,
            discount_pct=dpct, order_value=round(tot,0),
            coupon_used=("OFFER%d"%dpct if dpct else ""),
            payment=random.choices(["UPI","COD","Card"],[.6,.25,.15])[0],
            returned=random.random() < {"price_sensitive":0.22,"lapsing":0.10}.get(c._seg,0.05)))
        oid += 1
odf = pd.DataFrame(orders); idf = pd.DataFrame(items)

# ---- Cart / browsing events (abandonment signal)
ev = []
for _, c in cdf.iterrows():
    if c._seg == "dormant": continue
    for _ in range(np.random.poisson({"loyal":6,"regular":4,"price_sensitive":7,"new":4,"lapsing":3}[c._seg])):
        d = END - timedelta(days=int(np.random.exponential(14)))
        if d < START: continue
        pid = random.choice(list(products.product_id))
        et = random.choices(["view","add_to_cart","checkout_started"],[.55,.3,.15])[0]
        ev.append(dict(customer_id=c.customer_id, date=d, event=et, product_id=pid,
            converted_within_24h=random.random() < {"view":.05,"add_to_cart":.25,"checkout_started":.55}[et]*(0.7 if c._seg=="price_sensitive" else 1)))
edf = pd.DataFrame(ev)

# ---- Derived customer stats (what the engine would read)
g = odf.groupby("customer_id")
st = pd.DataFrame({"orders_total": g.size(), "lifetime_value": g.order_value.sum(),
    "avg_order_value": g.order_value.mean().round(0), "last_order_date": g.order_date.max(),
    "discount_order_share": g.apply(lambda x: (x.discount_pct>0).mean()).round(2)})
cdf = cdf.merge(st, left_on="customer_id", right_index=True, how="left")
cdf[["orders_total","lifetime_value","avg_order_value","discount_order_share"]] = cdf[["orders_total","lifetime_value","avg_order_value","discount_order_share"]].fillna(0)
cdf["days_since_last_order"] = cdf.last_order_date.apply(lambda d: (END-d).days if pd.notna(d) else None)
cdf["reward_points"] = (cdf.lifetime_value*0.02).round(0).astype(int)
cdf["last_order_date"] = cdf.last_order_date
cdf["tier"] = cdf.lifetime_value.apply(lambda v: "Gold" if v>=15000 else "Silver" if v>=6000 else "Bronze")
cdf["city_tier"] = cdf.city.map({"Delhi":"Tier 1","Gurugram":"Tier 1","Chandigarh":"Tier 2","Jaipur":"Tier 2","Lucknow":"Tier 2","Ludhiana":"Tier 2","Amritsar":"Tier 2","Mohali":"Tier 2","Jalandhar":"Tier 3","Patiala":"Tier 3"})
cdf["points_expiring"] = (cdf.reward_points*np.random.uniform(0,0.4,len(cdf))).round(0).astype(int)
rr = odf.groupby("customer_id").returned.mean().round(2)
cdf["return_rate"] = cdf.customer_id.map(rr).fillna(0)
rc = edf[(edf.event!="view")&(~edf.converted_within_24h)&(pd.to_datetime(edf.date)>=pd.Timestamp(END-timedelta(days=3)))]
cdf["cart_product_ids"] = cdf.customer_id.map(rc.groupby("customer_id").product_id.apply(lambda x: ",".join(sorted(set(x))))).fillna("")
cdf["cart_items"] = cdf.cart_product_ids.apply(lambda x: len(x.split(",")) if x else 0)
cdf = cdf.drop(columns=["_seg"])  # engine must infer segments, as in real life

# ---- Business rules starter sheet (marketing-editable)
rules = pd.DataFrame([
 ("max_discount_pct_global",20,"Never exceed this on any offer"),
 ("min_margin_pct_after_discount",15,"Offer blocked if margin falls below this"),
 ("max_discount_Makhana_pct",15,"Category cap"),
 ("max_discount_Nuts_pct",12,"Category cap (low margin)"),
 ("max_discount_Fasting_pct",10,"Category cap (low margin)"),
 ("cooldown_days_per_customer",7,"Min days between offers to same customer"),
 ("max_offers_per_customer_per_month",3,"Frequency cap"),
 ("min_stock_for_offer",20,"Do not promote products below this stock"),
 ("respect_marketing_opt_in",True,"Skip customers with opt-in = False"),
], columns=["rule","value","note"])

dict_ = pd.DataFrame([
 ("customers","One row per customer (derived stats included; segments are NOT labelled)"),
 ("products","Catalogue with cost, price, margin, stock and festival tag"),
 ("orders","Order header: date, discount used, value, payment"),
 ("order_items","Line items per order"),
 ("events","Browse / cart / checkout events with 24h conversion flag"),
 ("rules","Starter business limits the engine must obey"),
], columns=["sheet","description"])

out = "data/dryfruit_synthetic_data.xlsx"
with pd.ExcelWriter(out, engine="openpyxl") as w:
    dict_.to_excel(w,"README",index=False); cdf.to_excel(w,"customers",index=False)
    products.to_excel(w,"products",index=False); odf.to_excel(w,"orders",index=False)
    idf.to_excel(w,"order_items",index=False); edf.to_excel(w,"events",index=False)
    rules.to_excel(w,"rules",index=False)
    from openpyxl.styles import Font, PatternFill
    for ws in w.book.worksheets:
        for c in ws[1]: c.font = Font(name="Arial", bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="2F5D3A")
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(34, max(12, max(len(str(c.value or "")) for c in col[:50])+2))
        ws.freeze_panes = "A2"
print(len(cdf), len(odf), len(idf), len(edf))
