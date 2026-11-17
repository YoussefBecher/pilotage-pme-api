"""Données de démonstration : python seed.py (supprime et recrée les données)."""
import os, random, datetime as dt, psycopg
from main import hash_pw
random.seed(7)
today = dt.date.today()
PRODUITS = [("Huile d’olive 1 L","Épicerie",11.5,15.9),("Couscous 1 kg","Épicerie",2.1,3.2),("Harissa 380 g","Épicerie",1.8,2.9),("Sucre 1 kg","Épicerie",1.7,2.2),
 ("Café moulu 250 g","Boissons",4.8,7.5),("Eau minérale 1,5 L","Boissons",0.6,1.0),("Jus d’orange 1 L","Boissons",2.2,3.5),("Savon liquide 500 ml","Hygiène",3.1,4.8),
 ("Dentifrice 75 ml","Hygiène",2.4,3.9),("Lait UHT 1 L","Produits frais",1.3,1.8),("Yaourt nature x4","Produits frais",1.5,2.4),("Fromage râpé 200 g","Produits frais",3.6,5.5)]
PRE = ["Sana","Mehdi","Ines","Karim","Rania","Omar","Leila","Walid","Amira","Hatem","Nour","Slim"]
NOM = ["Ben Ali","Trabelsi","Gharbi","Jebali","Mansour","Hammami","Cherif","Bouzid","Karoui","Mejri"]
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    c.execute("TRUNCATE fact_ventes, stock, dim_client, dim_produit, users RESTART IDENTITY CASCADE")
    for p in PRODUITS: c.execute("INSERT INTO dim_produit(nom,categorie,prix_achat) VALUES (%s,%s,%s)", p[:3])
    for _ in range(150):
        c.execute("INSERT INTO dim_client(nom,created) VALUES (%s,%s)", (f"{random.choice(PRE)} {random.choice(NOM)}", today - dt.timedelta(days=random.randint(0, 400))))
    # Profils clients réalistes : fidèles, occasionnels, à risque (arrêt 35-80 j), perdus (arrêt > 95 j)
    prof = {}
    for cid in range(1, 151):
        x = random.random()
        prof[cid] = (8, 0) if x < .2 else (.3, 0) if x < .65 else (1, random.randint(35, 80)) if x < .85 else (1, random.randint(95, 170))
    ticket = 0
    with c.cursor().copy("COPY fact_ventes(date,ticket,produit_id,client_id,qte,montant,marge) FROM STDIN") as cp:
        for i in range(180):
            ago = 179 - i
            day = today - dt.timedelta(days=ago)
            ids = [k for k, v in prof.items() if v[1] <= ago]
            wts = [prof[k][0] for k in ids]
            for _ in range(int(random.randint(35, 55) * (1 + i / 600) * (1.25 if day.weekday() >= 4 else 1))):
                ticket += 1
                cl = random.choices(ids, wts)[0] if random.random() < .6 else None  # 40 % de tickets anonymes
                for pid in random.sample(range(1, 13), random.randint(1, 4)):
                    qte, (_, _, achat, prix) = random.randint(1, 3), PRODUITS[pid - 1]
                    cp.write_row((day, ticket, pid, cl, qte, round(prix * qte, 2), round((prix - achat) * qte, 2)))
    for pid in range(1, 13): c.execute("INSERT INTO stock VALUES (%s,%s)", (pid, 0 if pid == 5 else 14 if pid == 10 else random.randint(40, 400)))
    pw = os.getenv("DEMO_PASSWORD", "demo1234")
    c.execute("INSERT INTO users VALUES ('patron@demo.tn',%s,'patron'),('employe@demo.tn',%s,'employe')", (hash_pw(pw), hash_pw(pw)))
print("OK : patron@demo.tn / employe@demo.tn (mot de passe :", os.getenv("DEMO_PASSWORD", "demo1234"), ")")
